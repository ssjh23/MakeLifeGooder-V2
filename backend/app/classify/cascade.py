"""The classification cascade.

Four rungs, cheapest and most certain first. They map exactly onto
``transactions.classified_by``, which is not a coincidence: storing which rung
answered is what allows a prompt change to reprocess only the rows a model
produced and leave everything a person decided untouched.

  1. override           this user's own ruling for this descriptor
  2. alias              exact match in the shared alias table
  3. merchant_default   pg_trgm similarity above threshold
  4. llm                genuinely unseen descriptors only, batched

The economics follow from the order. Because rung four is reached only by
descriptors no tenant has ever seen, and because the alias table is
cross-tenant, cost tracks *distinct new merchants* rather than transaction
volume, and flattens as the user base grows. The tenth Singapore user shopping
at FairPrice costs nothing.

The order also degrades well. If the model provider is down, rungs one to three
still answer, so only genuinely new merchants queue and the product keeps
working (TC-REV-019).

===========================================================================
BUILD STEP 5.3 (rungs 1-3)   depends on: 0.2, 0.3, 5.1, 5.2
BUILD STEP 5.5 (rung 4)      depends on: 5.3, 5.4 claude adapter
Verify: uv run pytest tests/integration/test_cascade.py
===========================================================================
Build rungs one to three first and leave ``_llm`` raising. The cascade is
useful and fully testable without a model: with the fake adapter never called,
you can still prove short-circuiting, the transfer ordering, and the
cross-tenant cache. Add rung four only once 5.4 is done.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from app.classify.alias import AliasWriter
from app.classify.llm.base import LLMAdapter, LLMUnavailable
from app.classify.transfer import is_person_transfer
from app.db.models import AliasSource
from app.db.repositories import CategoryRepository, MerchantRepository
from app.telemetry import events, get_logger

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class Resolution:
    merchant_id: uuid.UUID | None
    category_id: uuid.UUID | None
    #: One of override, alias, merchant_default, llm. Stored on the row.
    rung: str
    confidence: float | None = None
    prompt_version: str | None = None


class CascadeResolver:
    def __init__(
        self,
        merchants: MerchantRepository,
        categories: CategoryRepository,
        alias_writer: AliasWriter,
        llm: LLMAdapter,
        *,
        trgm_threshold: float = 0.4,
    ) -> None:
        self._merchants = merchants
        self._categories = categories
        self._alias_writer = alias_writer
        #: Named distinctly from the ``_llm`` *method* below -- the two would
        #: otherwise collide, since an instance attribute set in __init__
        #: shadows a same-named method on every subsequent ``self._llm``
        #: lookup, silently turning ``self._llm(...)`` into "call the adapter
        #: object" instead of "call this rung".
        self._llm_adapter = llm
        self._threshold = trgm_threshold

    async def resolve_many(self, descriptor_keys: list[str]) -> dict[str, Resolution]:
        """Resolve a statement's descriptors in one pass.

        TODO for step 5.3:
          1. Drop person-to-person transfers first, with :meth:`_excluded`,
             before anything else touches them. This ordering is not an
             optimisation; see :mod:`app.classify.transfer`.
          2. For each remaining descriptor, try ``_override``, then ``_alias``,
             then ``_trgm``, stopping at the first hit.
          3. Collect whatever is still unresolved rather than calling the model
             per descriptor.
          4. Set ``rung`` on every resolution. It becomes ``classified_by``, and
             getting it wrong makes a later reprocess touch the wrong rows.

        TODO for step 5.5:
          5. Pass the unresolved remainder to :meth:`_llm` in one batch, if the
             remainder is non-empty. Do not call the model with an empty list.
          6. Write the results through the alias writer, so the next tenant to
             see these merchants pays nothing.

        Tests worth writing alongside:
          * each rung short-circuits, so an override means no alias lookup
          * a person's name appears in zero outbound payloads (TC-REV-012)
          * sixty unseen descriptors produce two calls, not sixty (TC-REV-009)
          * with ``FailingLLMAdapter``, known merchants still resolve and no
            user-facing 500 occurs (TC-REV-019)

        Batched rather than per row, which is what keeps rung four to a couple
        of calls per statement instead of one per transaction.
        """
        resolutions: dict[str, Resolution] = {}
        remaining: list[str] = []

        for descriptor_key in descriptor_keys:
            if self._excluded(descriptor_key):
                continue

            resolution = await self._override(descriptor_key)
            if resolution is None:
                resolution = await self._alias(descriptor_key)
            if resolution is None:
                resolution = await self._trgm(descriptor_key)

            if resolution is not None:
                resolutions[descriptor_key] = resolution
            else:
                remaining.append(descriptor_key)

        if remaining:
            resolutions.update(await self._llm(remaining))

        return resolutions

    async def _override(self, descriptor_key: str) -> Resolution | None:
        """Rung one. A person's own decision beats everything, always."""
        merchant_id = await self._merchants.find_override(descriptor_key)
        if merchant_id is None:
            return None
        return await self._resolution_for(merchant_id, rung="override", confidence=None)

    async def _alias(self, descriptor_key: str) -> Resolution | None:
        """Rung two. One indexed read: ``descriptor_key`` is globally unique,
        which is what the global alias cache buys."""
        merchant_id = await self._merchants.find_alias(descriptor_key)
        if merchant_id is None:
            return None
        return await self._resolution_for(merchant_id, rung="alias", confidence=None)

    async def _trgm(self, descriptor_key: str) -> Resolution | None:
        """Rung three. Trigram similarity above the threshold.

        String similarity, deliberately, not semantic similarity.
        """
        match = await self._merchants.find_similar_with_score(descriptor_key, self._threshold)
        if match is None:
            return None
        merchant_id, score = match
        return await self._resolution_for(
            merchant_id, rung="merchant_default", confidence=score
        )

    async def _llm(self, descriptor_keys: list[str]) -> dict[str, Resolution]:
        """Rung four. Only descriptors that reached here.

        Only the normalised strings cross the boundary. No amounts, no dates,
        no PDF, no identity, nothing that ties a merchant to a person.
        """
        try:
            suggestions = await self._llm_adapter.classify_batch(descriptor_keys)
        except LLMUnavailable:
            # An outage degrades classification; it never fails an import
            # (TC-REV-019). Rungs one to three already answered above, so
            # only genuinely new merchants stay unresolved here.
            logger.warning(events.LLM_UNAVAILABLE, descriptor_count=len(descriptor_keys))
            return {}

        requested = set(descriptor_keys)
        resolutions: dict[str, Resolution] = {}
        for suggestion in suggestions:
            if suggestion.descriptor_key not in requested:
                # A model returning something outside the batch is a signal,
                # not data -- never write it as though it were asked for.
                continue

            category = await self._categories.find_by_slug(suggestion.category_slug)
            # Reuse an existing merchant of the same name rather than always
            # minting a new one: a normaliser gap (or the same chain phrased
            # two different ways) can hand the model two different unseen
            # descriptors for a merchant that already has a row, and without
            # this check each one would get its own -- correctly named, but
            # a second identity splitting that merchant's history in two.
            merchant = await self._merchants.find_by_canonical_name(suggestion.canonical_name)
            if merchant is None:
                merchant = await self._merchants.create(
                    canonical_name=suggestion.canonical_name,
                    default_category_id=category.id if category is not None else None,
                )
            # So the next tenant to see this merchant pays nothing. Screened
            # internally against a confidence floor -- a weak guess still
            # classifies this row, once, but is not cached for anyone else.
            await self._alias_writer.upsert(
                descriptor_key=suggestion.descriptor_key,
                merchant_id=merchant.id,
                source=AliasSource.LLM.value,
                confidence=suggestion.confidence,
            )

            resolutions[suggestion.descriptor_key] = Resolution(
                merchant_id=merchant.id,
                category_id=category.id if category is not None else None,
                rung="llm",
                confidence=suggestion.confidence,
                prompt_version=self._llm_adapter.prompt_version,
            )

        return resolutions

    async def _resolution_for(
        self, merchant_id: uuid.UUID, *, rung: str, confidence: float | None
    ) -> Resolution:
        """The category a merchant carries once resolved, from its own
        ``default_category_id``.

        Known gap: a ``merchant_overrides`` row can independently carry its
        own ``category_id`` with no ``merchant_id`` -- a category-only
        correction -- and :meth:`~app.db.repositories.MerchantRepository.find_override`
        does not surface that column, so :meth:`_override` cannot resolve one
        yet. Flagged here rather than silently approximated.
        """
        merchant = await self._merchants.get(merchant_id)
        category_id = merchant.default_category_id if merchant is not None else None
        return Resolution(
            merchant_id=merchant_id, category_id=category_id, rung=rung, confidence=confidence
        )

    @staticmethod
    def _excluded(descriptor_key: str) -> bool:
        return is_person_transfer(descriptor_key)
