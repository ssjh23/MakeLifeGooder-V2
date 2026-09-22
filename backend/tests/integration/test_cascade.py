"""BUILD STEPS 5.1, 5.3 and 5.5: MerchantRepository and the cascade.

``TestFindOverride`` through ``TestUpsertAlias`` and ``TestAliasWriter``
exercise the data operations each rung depends on. ``TestCascadeResolver``
composes them into the actual cascade -- transfer filtering, short-circuit
ordering, and the LLM rung.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.classify.alias import AliasWriter
from app.classify.cascade import CascadeResolver
from app.classify.llm.base import MerchantSuggestion
from app.classify.llm.fake import FailingLLMAdapter, FakeLLMAdapter
from app.db.models import AliasSource, Merchant, MerchantAlias, MerchantOverride
from app.db.repositories import CategoryRepository, MerchantRepository, RuleRepository
from app.db.session import tenant_session
from app.extract.base import ParsedRow
from app.services._slug import slugify
from app.worker.tasks import aggregate_statement, classify_statement
from tests.integration.test_import_flow import (
    StubParser,
    _extract,
    _parsed,
    _register_a_statement,
    _seed_card,
    _unscoped_engine,
)

pytestmark = pytest.mark.integration


async def _seed_user(session: AsyncSession, user_id: uuid.UUID) -> None:
    await session.execute(
        text("INSERT INTO users (id, email, display_name) VALUES (:id, :email, 'Test User')"),
        {"id": user_id, "email": f"{user_id}@example.com"},
    )


async def _seed_merchant(
    session: AsyncSession, *, name: str = "McDonald's", default_category_id: uuid.UUID | None = None
) -> uuid.UUID:
    merchant = Merchant(
        canonical_name=name,
        name_key=slugify(name, fallback="merchant"),
        default_category_id=default_category_id,
    )
    session.add(merchant)
    await session.flush()
    return merchant.id


class TestFindOverride:
    @pytest.mark.p0
    async def test_returns_the_overriding_merchant_id(
        self, sessionmaker_for_app: async_sessionmaker[AsyncSession]
    ) -> None:
        user_id = uuid.uuid4()

        async with tenant_session(sessionmaker_for_app, user_id) as session:
            await _seed_user(session, user_id)
            merchant_id = await _seed_merchant(session)
            session.add(
                MerchantOverride(
                    user_id=user_id, descriptor_key="mcdonalds", merchant_id=merchant_id
                )
            )
            await session.flush()

            found = await MerchantRepository(session).find_override("mcdonalds")

            assert found == merchant_id

    async def test_returns_none_when_absent(
        self, sessionmaker_for_app: async_sessionmaker[AsyncSession]
    ) -> None:
        user_id = uuid.uuid4()

        async with tenant_session(sessionmaker_for_app, user_id) as session:
            await _seed_user(session, user_id)
            assert await MerchantRepository(session).find_override("mcdonalds") is None

    @pytest.mark.p0
    @pytest.mark.security
    async def test_is_scoped_to_the_tenant(
        self, sessionmaker_for_app: async_sessionmaker[AsyncSession]
    ) -> None:
        """A person's own ruling beats everything -- for that person. It must
        stay invisible to everyone else, which is the entire point of rung
        one living on a table row level security applies to."""
        owner = uuid.uuid4()
        stranger = uuid.uuid4()

        async with tenant_session(sessionmaker_for_app, owner) as session:
            await _seed_user(session, owner)
            merchant_id = await _seed_merchant(session)
            session.add(
                MerchantOverride(
                    user_id=owner, descriptor_key="mcdonalds", merchant_id=merchant_id
                )
            )
            await session.flush()

        async with tenant_session(sessionmaker_for_app, stranger) as session:
            await _seed_user(session, stranger)
            assert await MerchantRepository(session).find_override("mcdonalds") is None


class TestFindAlias:
    async def test_returns_the_aliased_merchant_id(
        self, sessionmaker_for_app: async_sessionmaker[AsyncSession]
    ) -> None:
        user_id = uuid.uuid4()

        async with tenant_session(sessionmaker_for_app, user_id) as session:
            await _seed_user(session, user_id)
            merchant_id = await _seed_merchant(session)
            session.add(
                MerchantAlias(
                    merchant_id=merchant_id,
                    descriptor_key="mcdonalds",
                    source=AliasSource.MANUAL,
                )
            )
            await session.flush()

            found = await MerchantRepository(session).find_alias("mcdonalds")

            assert found == merchant_id

    async def test_returns_none_when_absent(
        self, sessionmaker_for_app: async_sessionmaker[AsyncSession]
    ) -> None:
        user_id = uuid.uuid4()

        async with tenant_session(sessionmaker_for_app, user_id) as session:
            await _seed_user(session, user_id)
            assert await MerchantRepository(session).find_alias("mcdonalds") is None

    @pytest.mark.p1
    async def test_is_visible_across_tenants(
        self, sessionmaker_for_app: async_sessionmaker[AsyncSession]
    ) -> None:
        """Cross-tenant by design (ADR-006): the alias cache warms once for
        everybody, which is what makes the tenth Singapore user shopping at
        FairPrice cost nothing."""
        seeder = uuid.uuid4()
        reader = uuid.uuid4()

        async with tenant_session(sessionmaker_for_app, seeder) as session:
            await _seed_user(session, seeder)
            merchant_id = await _seed_merchant(session, name="FairPrice")
            session.add(
                MerchantAlias(
                    merchant_id=merchant_id,
                    descriptor_key="fairprice",
                    source=AliasSource.RULE,
                )
            )
            await session.flush()

        async with tenant_session(sessionmaker_for_app, reader) as session:
            await _seed_user(session, reader)
            found = await MerchantRepository(session).find_alias("fairprice")

            assert found == merchant_id


class TestFindSimilar:
    async def _seeded(
        self, session: AsyncSession, *, descriptor_key: str, name: str
    ) -> uuid.UUID:
        merchant_id = await _seed_merchant(session, name=name)
        session.add(
            MerchantAlias(
                merchant_id=merchant_id, descriptor_key=descriptor_key, source=AliasSource.MANUAL
            )
        )
        await session.flush()
        return merchant_id

    @pytest.mark.p1
    async def test_matches_a_close_descriptor_above_threshold(
        self, sessionmaker_for_app: async_sessionmaker[AsyncSession]
    ) -> None:
        user_id = uuid.uuid4()

        async with tenant_session(sessionmaker_for_app, user_id) as session:
            await _seed_user(session, user_id)
            merchant_id = await self._seeded(
                session, descriptor_key="mcdonalds", name="McDonald's"
            )

            found = await MerchantRepository(session).find_similar("mcdonald", threshold=0.4)

            assert found == merchant_id

    async def test_returns_none_below_threshold(
        self, sessionmaker_for_app: async_sessionmaker[AsyncSession]
    ) -> None:
        user_id = uuid.uuid4()

        async with tenant_session(sessionmaker_for_app, user_id) as session:
            await _seed_user(session, user_id)
            await self._seeded(session, descriptor_key="mcdonalds", name="McDonald's")

            found = await MerchantRepository(session).find_similar(
                "a completely unrelated string", threshold=0.4
            )

            assert found is None

    async def test_picks_the_closest_match_when_several_clear_the_threshold(
        self, sessionmaker_for_app: async_sessionmaker[AsyncSession]
    ) -> None:
        user_id = uuid.uuid4()

        async with tenant_session(sessionmaker_for_app, user_id) as session:
            await _seed_user(session, user_id)
            await self._seeded(session, descriptor_key="mcdonald", name="Not Quite")
            exact_ish = await self._seeded(
                session, descriptor_key="mcdonalds", name="McDonald's"
            )

            found = await MerchantRepository(session).find_similar("mcdonalds", threshold=0.1)

            assert found == exact_ish


class TestUpsertAlias:
    async def test_inserts_a_new_alias(
        self, sessionmaker_for_app: async_sessionmaker[AsyncSession]
    ) -> None:
        user_id = uuid.uuid4()

        async with tenant_session(sessionmaker_for_app, user_id) as session:
            await _seed_user(session, user_id)
            merchant_id = await _seed_merchant(session)

            await MerchantRepository(session).upsert_alias(
                descriptor_key="mcdonalds",
                merchant_id=merchant_id,
                source=AliasSource.LLM.value,
                confidence=0.92,
            )

            found = await MerchantRepository(session).find_alias("mcdonalds")
            assert found == merchant_id

    @pytest.mark.p0
    async def test_a_second_call_overwrites_the_first(
        self, sessionmaker_for_app: async_sessionmaker[AsyncSession]
    ) -> None:
        """The write itself is unconditional -- whether it should happen is a
        policy question for AliasWriter (BUILD STEP 5.2), not this method."""
        user_id = uuid.uuid4()

        async with tenant_session(sessionmaker_for_app, user_id) as session:
            await _seed_user(session, user_id)
            first_merchant = await _seed_merchant(session, name="Guess One")
            second_merchant = await _seed_merchant(session, name="Guess Two")

            repo = MerchantRepository(session)
            await repo.upsert_alias(
                descriptor_key="ambiguous",
                merchant_id=first_merchant,
                source=AliasSource.LLM.value,
                confidence=0.5,
            )
            await repo.upsert_alias(
                descriptor_key="ambiguous",
                merchant_id=second_merchant,
                source=AliasSource.MANUAL.value,
                confidence=None,
            )

            found = await repo.find_alias("ambiguous")
            assert found == second_merchant

            row = (
                await session.execute(
                    text(
                        "SELECT source, confidence FROM merchant_aliases "
                        "WHERE descriptor_key = :key"
                    ),
                    {"key": "ambiguous"},
                )
            ).one()
            assert row.source == "manual"
            assert row.confidence is None


class TestAliasWriter:
    """BUILD STEP 5.2. Policy in front of ``MerchantRepository.upsert_alias``."""

    async def test_writes_a_high_confidence_alias(
        self, sessionmaker_for_app: async_sessionmaker[AsyncSession]
    ) -> None:
        user_id = uuid.uuid4()

        async with tenant_session(sessionmaker_for_app, user_id) as session:
            await _seed_user(session, user_id)
            merchant_id = await _seed_merchant(session)
            merchants = MerchantRepository(session)

            await AliasWriter(merchants).upsert(
                descriptor_key="mcdonalds",
                merchant_id=merchant_id,
                source=AliasSource.LLM.value,
                confidence=0.92,
            )

            assert await merchants.find_alias("mcdonalds") == merchant_id

    async def test_writes_a_manual_alias_with_no_confidence(
        self, sessionmaker_for_app: async_sessionmaker[AsyncSession]
    ) -> None:
        """A confidence-less write (``rule`` or ``manual``) is never screened
        by the model-confidence threshold -- there is no model score to
        distrust."""
        user_id = uuid.uuid4()

        async with tenant_session(sessionmaker_for_app, user_id) as session:
            await _seed_user(session, user_id)
            merchant_id = await _seed_merchant(session)
            merchants = MerchantRepository(session)

            await AliasWriter(merchants).upsert(
                descriptor_key="mcdonalds",
                merchant_id=merchant_id,
                source=AliasSource.MANUAL.value,
                confidence=None,
            )

            assert await merchants.find_alias("mcdonalds") == merchant_id

    @pytest.mark.p0
    @pytest.mark.security
    async def test_does_not_write_a_low_confidence_model_guess(
        self, sessionmaker_for_app: async_sessionmaker[AsyncSession]
    ) -> None:
        """A weak model guess must not become every future tenant's answer."""
        user_id = uuid.uuid4()

        async with tenant_session(sessionmaker_for_app, user_id) as session:
            await _seed_user(session, user_id)
            merchant_id = await _seed_merchant(session)
            merchants = MerchantRepository(session)

            await AliasWriter(merchants).upsert(
                descriptor_key="mcdonalds",
                merchant_id=merchant_id,
                source=AliasSource.LLM.value,
                confidence=0.5,
            )

            assert await merchants.find_alias("mcdonalds") is None

    async def test_refuses_an_empty_descriptor_key(
        self, sessionmaker_for_app: async_sessionmaker[AsyncSession]
    ) -> None:
        user_id = uuid.uuid4()

        async with tenant_session(sessionmaker_for_app, user_id) as session:
            await _seed_user(session, user_id)
            merchant_id = await _seed_merchant(session)
            merchants = MerchantRepository(session)

            with pytest.raises(ValueError):
                await AliasWriter(merchants).upsert(
                    descriptor_key="   ",
                    merchant_id=merchant_id,
                    source=AliasSource.MANUAL.value,
                    confidence=None,
                )

    @pytest.mark.p0
    @pytest.mark.security
    async def test_refuses_a_person_transfer_descriptor(
        self, sessionmaker_for_app: async_sessionmaker[AsyncSession]
    ) -> None:
        """The last point at which a person's name can be stopped from
        entering the cross-tenant cache (TC-REV-012)."""
        user_id = uuid.uuid4()

        async with tenant_session(sessionmaker_for_app, user_id) as session:
            await _seed_user(session, user_id)
            merchant_id = await _seed_merchant(session, name="A Person")
            merchants = MerchantRepository(session)

            with pytest.raises(AssertionError):
                await AliasWriter(merchants).upsert(
                    descriptor_key="PAYNOW TRANSFER JOHN TAN",
                    merchant_id=merchant_id,
                    source=AliasSource.LLM.value,
                    confidence=0.95,
                )

            assert await merchants.find_alias("PAYNOW TRANSFER JOHN TAN") is None

    @pytest.mark.p1
    async def test_TC_REV_010_alias_written_by_one_tenant_is_visible_to_another(
        self, sessionmaker_for_app: async_sessionmaker[AsyncSession]
    ) -> None:
        """User A classifies FAIRPRICE; user B's import sees it already
        resolved. This is the mechanism the cost claim in the capacity model
        depends on -- that the alias cache warms once for everybody. Proving
        that B's import triggers zero model calls needs the cascade
        (BUILD STEP 5.3/5.5), tested in ``TestCascadeResolver`` below; this
        pins the write-then-read half of the claim on its own."""
        seer, reader = uuid.uuid4(), uuid.uuid4()

        async with tenant_session(sessionmaker_for_app, seer) as session:
            await _seed_user(session, seer)
            merchant_id = await _seed_merchant(session, name="FairPrice")
            await AliasWriter(MerchantRepository(session)).upsert(
                descriptor_key="fairprice",
                merchant_id=merchant_id,
                source=AliasSource.LLM.value,
                confidence=0.95,
            )

        async with tenant_session(sessionmaker_for_app, reader) as session:
            await _seed_user(session, reader)
            found = await MerchantRepository(session).find_alias("fairprice")

            assert found == merchant_id


@dataclass
class DecliningLLMAdapter:
    """Answers only the descriptors named in ``answers``.

    A model that "returns categories from the known taxonomy" (the protocol's
    own requirement) may still legitimately answer fewer than it was asked --
    this is what a partial answer, not an outage, looks like to the cascade.
    """

    answers: dict[str, str]
    prompt_version: str = "declining-v1"
    calls: list[list[str]] = field(default_factory=list)

    async def classify_batch(self, descriptor_keys: list[str]) -> list[MerchantSuggestion]:
        self.calls.append(list(descriptor_keys))
        return [
            MerchantSuggestion(
                descriptor_key=key,
                canonical_name=key.title(),
                category_slug=self.answers[key],
                confidence=0.9,
            )
            for key in descriptor_keys
            if key in self.answers
        ]


@dataclass
class _LowConfidenceLLM:
    """Wraps a real adapter and forces every confidence down, so the alias
    writer's confidence floor (0.7) is exercised without depending on
    :class:`FakeLLMAdapter`'s hash-derived value happening to land below it."""

    inner: FakeLLMAdapter
    prompt_version: str = "low-confidence-v1"

    async def classify_batch(self, descriptor_keys: list[str]) -> list[MerchantSuggestion]:
        suggestions = await self.inner.classify_batch(descriptor_keys)
        return [
            MerchantSuggestion(
                descriptor_key=s.descriptor_key,
                canonical_name=s.canonical_name,
                category_slug=s.category_slug,
                confidence=0.5,
            )
            for s in suggestions
        ]


@dataclass
class _FixedNameLLMAdapter:
    """Returns an exact, caller-chosen ``canonical_name`` per descriptor.

    ``FakeLLMAdapter`` derives its name via ``key.replace("-", " ").title()``,
    which means it can never itself produce two suggestions whose names
    differ only in punctuation -- any hyphen in the descriptor key becomes a
    space before either suggestion is built. This adapter exists so a test
    can force exactly that: two suggestions naming the same merchant with
    genuinely different casing or hyphenation in the *name itself*, not the
    descriptor key.
    """

    names: dict[str, str]
    category_slug: str = "shopping"
    prompt_version: str = "fixed-name-v1"

    async def classify_batch(self, descriptor_keys: list[str]) -> list[MerchantSuggestion]:
        return [
            MerchantSuggestion(
                descriptor_key=key,
                canonical_name=self.names[key],
                category_slug=self.category_slug,
                confidence=0.9,
            )
            for key in descriptor_keys
            if key in self.names
        ]


def _resolver(
    session: AsyncSession, llm: object, *, trgm_threshold: float = 0.4
) -> CascadeResolver:
    merchants = MerchantRepository(session)
    return CascadeResolver(
        merchants,
        CategoryRepository(session),
        AliasWriter(merchants),
        llm,  # type: ignore[arg-type]
        trgm_threshold=trgm_threshold,
    )


class TestCascadeResolver:
    """BUILD STEPS 5.3 and 5.5: the composed cascade."""

    @pytest.mark.p0
    async def test_override_short_circuits_before_alias_and_trgm(
        self, sessionmaker_for_app: async_sessionmaker[AsyncSession]
    ) -> None:
        user_id = uuid.uuid4()

        async with tenant_session(sessionmaker_for_app, user_id) as session:
            await _seed_user(session, user_id)
            overridden = await _seed_merchant(session, name="My Choice")
            aliased = await _seed_merchant(session, name="Wrong Choice")
            session.add(
                MerchantOverride(
                    user_id=user_id, descriptor_key="mcdonalds", merchant_id=overridden
                )
            )
            session.add(
                MerchantAlias(
                    merchant_id=aliased, descriptor_key="mcdonalds", source=AliasSource.MANUAL
                )
            )
            await session.flush()

            resolutions = await _resolver(session, FakeLLMAdapter()).resolve_many(["mcdonalds"])

            resolution = resolutions["mcdonalds"]
            assert resolution.merchant_id == overridden
            assert resolution.rung == "override"
            assert resolution.confidence is None

    @pytest.mark.p0
    async def test_alias_short_circuits_before_trgm(
        self, sessionmaker_for_app: async_sessionmaker[AsyncSession]
    ) -> None:
        user_id = uuid.uuid4()

        async with tenant_session(sessionmaker_for_app, user_id) as session:
            await _seed_user(session, user_id)
            exact = await _seed_merchant(session, name="Exact Alias")
            session.add(
                MerchantAlias(
                    merchant_id=exact, descriptor_key="mcdonalds", source=AliasSource.MANUAL
                )
            )
            await session.flush()

            resolutions = await _resolver(session, FakeLLMAdapter()).resolve_many(["mcdonalds"])

            resolution = resolutions["mcdonalds"]
            assert resolution.merchant_id == exact
            assert resolution.rung == "alias"

    async def test_trgm_resolves_a_fuzzy_match_with_its_similarity_as_confidence(
        self, sessionmaker_for_app: async_sessionmaker[AsyncSession]
    ) -> None:
        user_id = uuid.uuid4()

        async with tenant_session(sessionmaker_for_app, user_id) as session:
            await _seed_user(session, user_id)
            merchant_id = await _seed_merchant(session, name="McDonald's")
            session.add(
                MerchantAlias(
                    merchant_id=merchant_id, descriptor_key="mcdonalds", source=AliasSource.MANUAL
                )
            )
            await session.flush()

            resolutions = await _resolver(session, FakeLLMAdapter()).resolve_many(["mcdonald"])

            resolution = resolutions["mcdonald"]
            assert resolution.merchant_id == merchant_id
            assert resolution.rung == "merchant_default"
            assert resolution.confidence is not None
            assert 0.4 <= resolution.confidence <= 1.0

    @pytest.mark.p0
    @pytest.mark.security
    async def test_TC_REV_012_a_person_transfer_is_excluded_and_never_reaches_the_model(
        self, sessionmaker_for_app: async_sessionmaker[AsyncSession]
    ) -> None:
        user_id = uuid.uuid4()

        async with tenant_session(sessionmaker_for_app, user_id) as session:
            await _seed_user(session, user_id)
            llm = FakeLLMAdapter()

            resolutions = await _resolver(session, llm).resolve_many(
                ["PAYNOW TRANSFER JOHN TAN"]
            )

            assert resolutions == {}
            assert llm.calls == []

    @pytest.mark.p0
    async def test_an_unseen_descriptor_resolves_via_the_model_and_caches_for_the_next_tenant(
        self, sessionmaker_for_app: async_sessionmaker[AsyncSession]
    ) -> None:
        seer, reader = uuid.uuid4(), uuid.uuid4()
        llm = FakeLLMAdapter(overrides={"fairprice": "groceries"})

        async with tenant_session(sessionmaker_for_app, seer) as session:
            await _seed_user(session, seer)
            resolutions = await _resolver(session, llm).resolve_many(["fairprice"])

            resolution = resolutions["fairprice"]
            assert resolution.rung == "llm"
            assert resolution.prompt_version == llm.prompt_version
            assert llm.calls == [["fairprice"]]

        # TC-REV-010: the next tenant's import makes no model call at all.
        async with tenant_session(sessionmaker_for_app, reader) as session:
            await _seed_user(session, reader)
            found = await MerchantRepository(session).find_alias("fairprice")
            assert found == resolution.merchant_id
            assert llm.calls == [["fairprice"]]

    @pytest.mark.p0
    async def test_TC_REV_019_a_provider_outage_leaves_only_new_merchants_unresolved(
        self, sessionmaker_for_app: async_sessionmaker[AsyncSession]
    ) -> None:
        """Known merchants still resolve; the import never fails outright."""
        user_id = uuid.uuid4()

        async with tenant_session(sessionmaker_for_app, user_id) as session:
            await _seed_user(session, user_id)
            merchant_id = await _seed_merchant(session)
            session.add(
                MerchantOverride(
                    user_id=user_id, descriptor_key="mcdonalds", merchant_id=merchant_id
                )
            )
            await session.flush()

            resolutions = await _resolver(session, FailingLLMAdapter()).resolve_many(
                ["mcdonalds", "some-new-shop"]
            )

            assert resolutions["mcdonalds"].rung == "override"
            assert "some-new-shop" not in resolutions

    async def test_a_declined_descriptor_stays_absent_rather_than_fabricated(
        self, sessionmaker_for_app: async_sessionmaker[AsyncSession]
    ) -> None:
        user_id = uuid.uuid4()
        llm = DecliningLLMAdapter(answers={"fairprice": "groceries"})

        async with tenant_session(sessionmaker_for_app, user_id) as session:
            await _seed_user(session, user_id)

            resolutions = await _resolver(session, llm).resolve_many(
                ["fairprice", "unrecognisable-shop"]
            )

            assert resolutions["fairprice"].rung == "llm"
            assert "unrecognisable-shop" not in resolutions

    async def test_a_low_confidence_llm_guess_classifies_the_row_but_is_not_cached(
        self, sessionmaker_for_app: async_sessionmaker[AsyncSession]
    ) -> None:
        user_id = uuid.uuid4()
        llm = FakeLLMAdapter(overrides={"ambiguous-shop": "other"})

        async with tenant_session(sessionmaker_for_app, user_id) as session:
            await _seed_user(session, user_id)
            merchants = MerchantRepository(session)
            resolver = CascadeResolver(
                merchants,
                CategoryRepository(session),
                AliasWriter(merchants),
                _LowConfidenceLLM(llm),  # type: ignore[arg-type]
            )

            resolutions = await resolver.resolve_many(["ambiguous-shop"])

            assert resolutions["ambiguous-shop"].rung == "llm"
            assert resolutions["ambiguous-shop"].confidence == 0.5
            assert await merchants.find_alias("ambiguous-shop") is None

    @pytest.mark.p0
    async def test_two_new_descriptors_naming_the_same_merchant_share_one_merchant_row(
        self, sessionmaker_for_app: async_sessionmaker[AsyncSession]
    ) -> None:
        """A normaliser gap -- or simply two different phrasings of one
        chain -- can hand the model two genuinely different unseen
        descriptors for what is actually one real-world merchant. Reusing
        an existing merchant by canonical name (rather than always minting a
        new one) is the safety net for whichever case the corpus hasn't
        caught yet, so the two still resolve to one ``merchant_id`` instead
        of splitting one merchant's history across two rows."""
        user_id = uuid.uuid4()
        llm = FakeLLMAdapter()

        async with tenant_session(sessionmaker_for_app, user_id) as session:
            await _seed_user(session, user_id)
            # FakeLLMAdapter derives canonical_name from the descriptor key
            # itself (``key.replace("-", " ").title()``), so a hyphenated and
            # a spaced form of the same words land on the same name without
            # depending on real model output.
            resolutions = await _resolver(session, llm).resolve_many(
                ["zzz-transit-line-a", "zzz transit line a"]
            )

            assert resolutions["zzz-transit-line-a"].rung == "llm"
            assert resolutions["zzz transit line a"].rung == "llm"
            assert (
                resolutions["zzz-transit-line-a"].merchant_id
                == resolutions["zzz transit line a"].merchant_id
            )

            merchants = MerchantRepository(session)
            assert await merchants.find_alias("zzz-transit-line-a") is not None
            assert await merchants.find_alias("zzz transit line a") is not None

    @pytest.mark.p0
    async def test_hyphenated_and_spaced_canonical_names_share_one_merchant(
        self, sessionmaker_for_app: async_sessionmaker[AsyncSession]
    ) -> None:
        """The gap the test above doesn't cover: two suggestions whose
        *names* differ only in casing or hyphenation ("Old Tea Hut" vs
        "OLD-TEA-HUT"), from two descriptor keys that share no similar
        wording at all. Only a normalised name_key catches this -- exact
        case-insensitive equality would not."""
        user_id = uuid.uuid4()
        llm = _FixedNameLLMAdapter(
            names={"zzz-old-tea": "OLD-TEA-HUT", "zzz-tea-hut": "Old Tea Hut"}
        )

        async with tenant_session(sessionmaker_for_app, user_id) as session:
            await _seed_user(session, user_id)
            resolutions = await _resolver(session, llm).resolve_many(
                ["zzz-old-tea", "zzz-tea-hut"]
            )

            assert resolutions["zzz-old-tea"].rung == "llm"
            assert resolutions["zzz-tea-hut"].rung == "llm"
            assert (
                resolutions["zzz-old-tea"].merchant_id
                == resolutions["zzz-tea-hut"].merchant_id
            )

    async def test_genuinely_different_merchant_names_still_get_separate_rows(
        self, sessionmaker_for_app: async_sessionmaker[AsyncSession]
    ) -> None:
        """The guard-rail: normalising casing and hyphenation must not
        become normalising meaning. Two unrelated names stay two merchants."""
        user_id = uuid.uuid4()
        llm = _FixedNameLLMAdapter(
            names={"zzz-old-tea": "Old Tea Hut", "zzz-new-tea": "New Tea Hut"}
        )

        async with tenant_session(sessionmaker_for_app, user_id) as session:
            await _seed_user(session, user_id)
            resolutions = await _resolver(session, llm).resolve_many(
                ["zzz-old-tea", "zzz-new-tea"]
            )

            assert (
                resolutions["zzz-old-tea"].merchant_id
                != resolutions["zzz-new-tea"].merchant_id
            )


class TestClassifyAndAggregateWorkerTasks:
    """BUILD STEPS 5.5 and 7.1: the worker handlers, called directly, no
    queue -- the same pattern ``test_import_flow.py`` uses for extraction."""

    @pytest.mark.p0
    async def test_a_committed_statement_is_classified_and_aggregated(
        self, client: AsyncClient, auth: dict[str, str], user_a: dict[str, str]
    ) -> None:
        card_id = await _seed_card(user_a["id"])
        statement_id = await _register_a_statement(client, auth, card_id=card_id)
        parsed = _parsed(
            rows=[
                ParsedRow(
                    posted_on=date(2026, 7, 22),
                    description_raw="FAIRPRICE FINEST NEX",
                    amount_minor=1000,
                )
            ],
            printed_total_minor=1000,
        )
        await _extract(statement_id, user_a["id"], StubParser(result=parsed))

        commit = await client.post(
            f"/api/v1/statements/{statement_id}/commit", json={}, headers=auth
        )
        assert commit.status_code == 200, commit.text

        await classify_statement(
            statement_id=statement_id, user_id=user_a["id"], llm=FakeLLMAdapter()
        )
        await aggregate_statement(statement_id=statement_id, user_id=user_a["id"])

        # transactions and category_monthly_totals carry FORCE ROW LEVEL
        # SECURITY (TENANT_TABLES), which applies even to this owning-role
        # connection: app.user_id has to be set on it, exactly as _seed_card
        # does, or the policy matches nothing and every row is invisible.
        engine = await _unscoped_engine()
        try:
            async with engine.begin() as connection:
                await connection.execute(
                    text("SELECT set_config('app.user_id', :user_id, true)"),
                    {"user_id": user_a["id"]},
                )
                row = (
                    await connection.execute(
                        text(
                            "SELECT category_id, classified_by, merchant_id, descriptor_key "
                            "FROM transactions WHERE statement_id = CAST(:id AS uuid)"
                        ),
                        {"id": statement_id},
                    )
                ).one()
                total_row_count = await connection.scalar(
                    text(
                        "SELECT count(*) FROM category_monthly_totals WHERE category_id = :cat"
                    ),
                    {"cat": row.category_id},
                )
        finally:
            await engine.dispose()

        assert row.descriptor_key
        assert row.classified_by == "llm"
        assert row.category_id is not None
        assert row.merchant_id is not None
        assert total_row_count == 1


async def _system_category_id(slug: str) -> str:
    """Duplicated from ``test_dashboard.py`` rather than imported: that
    module imports ``test_review``, which imports ``DecliningLLMAdapter``
    from this one, so importing it here would be a circular import."""
    engine = await _unscoped_engine()
    try:
        async with engine.connect() as connection:
            return str(
                await connection.scalar(
                    text("SELECT id FROM categories WHERE slug = :slug AND user_id IS NULL"),
                    {"slug": slug},
                )
            )
    finally:
        await engine.dispose()


async def _row_after_classify(user_id: str, statement_id: str) -> object:
    """One row's classification fields, read directly -- mirrors the inline
    query ``test_a_committed_statement_is_classified_and_aggregated`` above
    already uses, factored out since the tests below need it three times."""
    engine = await _unscoped_engine()
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text("SELECT set_config('app.user_id', :user_id, true)"),
                {"user_id": user_id},
            )
            return (
                await connection.execute(
                    text(
                        "SELECT category_id, classified_by, merchant_id, rule_id "
                        "FROM transactions WHERE statement_id = CAST(:id AS uuid)"
                    ),
                    {"id": statement_id},
                )
            ).one()
    finally:
        await engine.dispose()


class TestRulesInTheCascade:
    """The classification pipeline (BUILD STEP 5.5) checking a standing rule
    against every row, not just the descriptor_key the cascade itself
    resolves -- see app/classify/pipeline.py's classify()."""

    @pytest.mark.p0
    async def test_a_standing_rule_matches_a_new_row_via_raw_text_only(
        self,
        client: AsyncClient,
        auth: dict[str, str],
        user_a: dict[str, str],
        sessionmaker_for_app: async_sessionmaker[AsyncSession],
    ) -> None:
        category_id = await _system_category_id("transport")
        async with tenant_session(sessionmaker_for_app, uuid.UUID(user_a["id"])) as session:
            rule = await RuleRepository(session).create(
                pattern="old tea hut",
                match_type="contains",
                category_id=uuid.UUID(category_id),
                scope="future",
                ignore_case=True,
                match_negative=False,
            )
            rule_id = rule.id

        card_id = await _seed_card(user_a["id"])
        statement_id = await _register_a_statement(client, auth, card_id=card_id)
        parsed = _parsed(
            rows=[
                ParsedRow(
                    posted_on=date(2026, 7, 22),
                    description_raw="SMP**OLD TEA HUT (CHANGI SG Ref No. : 745123456",
                    amount_minor=1000,
                )
            ],
            printed_total_minor=1000,
        )
        await _extract(statement_id, user_a["id"], StubParser(result=parsed))
        commit = await client.post(
            f"/api/v1/statements/{statement_id}/commit", json={}, headers=auth
        )
        assert commit.status_code == 200, commit.text

        await classify_statement(
            statement_id=statement_id, user_id=user_a["id"], llm=DecliningLLMAdapter(answers={})
        )

        row = await _row_after_classify(user_a["id"], statement_id)
        assert row.category_id == uuid.UUID(category_id)
        assert row.rule_id == rule_id
        assert row.classified_by is None  # a rule never invents a cascade rung

    async def test_override_still_wins_over_a_matching_rule(
        self,
        client: AsyncClient,
        auth: dict[str, str],
        user_a: dict[str, str],
        sessionmaker_for_app: async_sessionmaker[AsyncSession],
    ) -> None:
        override_category = await _system_category_id("groceries")
        rule_category = await _system_category_id("transport")
        async with tenant_session(sessionmaker_for_app, uuid.UUID(user_a["id"])) as session:
            # The override rung resolves its category from the merchant's own
            # default_category_id, not a category_id on the override row
            # itself (MerchantOverride.category_id exists for a category-only
            # correction, which _override()/_resolution_for() doesn't
            # currently read at all -- a separate, pre-existing gap, not
            # something this test is about).
            merchant_id = await _seed_merchant(
                session, name="Old Tea Hut", default_category_id=uuid.UUID(override_category)
            )
            session.add(
                MerchantOverride(
                    user_id=uuid.UUID(user_a["id"]), descriptor_key="smp", merchant_id=merchant_id
                )
            )
            await RuleRepository(session).create(
                pattern="old tea hut",
                match_type="contains",
                category_id=uuid.UUID(rule_category),
                scope="future",
                ignore_case=True,
                match_negative=False,
            )

        card_id = await _seed_card(user_a["id"])
        statement_id = await _register_a_statement(client, auth, card_id=card_id)
        parsed = _parsed(
            rows=[
                ParsedRow(
                    posted_on=date(2026, 7, 22),
                    description_raw="SMP**OLD TEA HUT (CHANGI SG Ref No. : 745123456",
                    amount_minor=1000,
                )
            ],
            printed_total_minor=1000,
        )
        await _extract(statement_id, user_a["id"], StubParser(result=parsed))
        commit = await client.post(
            f"/api/v1/statements/{statement_id}/commit", json={}, headers=auth
        )
        assert commit.status_code == 200, commit.text

        await classify_statement(
            statement_id=statement_id, user_id=user_a["id"], llm=DecliningLLMAdapter(answers={})
        )

        row = await _row_after_classify(user_a["id"], statement_id)
        assert row.classified_by == "override"
        assert row.category_id == uuid.UUID(override_category)
        assert row.rule_id is None

    async def test_a_rule_wins_over_the_llm_rung(
        self,
        client: AsyncClient,
        auth: dict[str, str],
        user_a: dict[str, str],
        sessionmaker_for_app: async_sessionmaker[AsyncSession],
    ) -> None:
        rule_category = await _system_category_id("transport")
        async with tenant_session(sessionmaker_for_app, uuid.UUID(user_a["id"])) as session:
            rule = await RuleRepository(session).create(
                pattern="old tea hut",
                match_type="contains",
                category_id=uuid.UUID(rule_category),
                scope="future",
                ignore_case=True,
                match_negative=False,
            )
            rule_id = rule.id

        card_id = await _seed_card(user_a["id"])
        statement_id = await _register_a_statement(client, auth, card_id=card_id)
        parsed = _parsed(
            rows=[
                ParsedRow(
                    posted_on=date(2026, 7, 22),
                    description_raw="SMP**OLD TEA HUT (CHANGI SG Ref No. : 745123456",
                    amount_minor=1000,
                )
            ],
            printed_total_minor=1000,
        )
        await _extract(statement_id, user_a["id"], StubParser(result=parsed))
        commit = await client.post(
            f"/api/v1/statements/{statement_id}/commit", json={}, headers=auth
        )
        assert commit.status_code == 200, commit.text

        # The LLM would assign "smp" (the descriptor_key) whatever its hash
        # picks; the rule's own category, over "transport", is what proves
        # the rule -- not the LLM's guess -- decided the category here.
        await classify_statement(
            statement_id=statement_id,
            user_id=user_a["id"],
            llm=FakeLLMAdapter(overrides={"smp": "groceries"}),
        )

        row = await _row_after_classify(user_a["id"], statement_id)
        assert row.classified_by == "llm"  # the model's own rung, left untouched
        assert row.merchant_id is not None  # the LLM's resolution, also left untouched
        assert row.category_id == uuid.UUID(rule_category)  # but the rule's category wins
        assert row.rule_id == rule_id

    async def test_two_matching_rules_the_longer_pattern_wins(
        self,
        client: AsyncClient,
        auth: dict[str, str],
        user_a: dict[str, str],
        sessionmaker_for_app: async_sessionmaker[AsyncSession],
    ) -> None:
        broad_category = await _system_category_id("groceries")
        specific_category = await _system_category_id("transport")
        async with tenant_session(sessionmaker_for_app, uuid.UUID(user_a["id"])) as session:
            repo = RuleRepository(session)
            await repo.create(
                pattern="smp",
                match_type="contains",
                category_id=uuid.UUID(broad_category),
                scope="future",
                ignore_case=True,
                match_negative=False,
            )
            specific = await repo.create(
                pattern="old tea hut",
                match_type="contains",
                category_id=uuid.UUID(specific_category),
                scope="future",
                ignore_case=True,
                match_negative=False,
            )
            specific_id = specific.id

        card_id = await _seed_card(user_a["id"])
        statement_id = await _register_a_statement(client, auth, card_id=card_id)
        parsed = _parsed(
            rows=[
                ParsedRow(
                    posted_on=date(2026, 7, 22),
                    description_raw="SMP**OLD TEA HUT (CHANGI SG Ref No. : 745123456",
                    amount_minor=1000,
                )
            ],
            printed_total_minor=1000,
        )
        await _extract(statement_id, user_a["id"], StubParser(result=parsed))
        commit = await client.post(
            f"/api/v1/statements/{statement_id}/commit", json={}, headers=auth
        )
        assert commit.status_code == 200, commit.text

        await classify_statement(
            statement_id=statement_id, user_id=user_a["id"], llm=DecliningLLMAdapter(answers={})
        )

        row = await _row_after_classify(user_a["id"], statement_id)
        assert row.category_id == uuid.UUID(specific_category)
        assert row.rule_id == specific_id
