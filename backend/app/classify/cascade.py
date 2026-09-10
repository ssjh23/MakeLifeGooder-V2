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

from app.classify.llm.base import LLMAdapter
from app.classify.transfer import is_person_transfer
from app.db.repositories import MerchantRepository


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
        llm: LLMAdapter,
        *,
        trgm_threshold: float = 0.4,
    ) -> None:
        self._merchants = merchants
        self._llm = llm
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
        raise NotImplementedError

    async def _override(self, descriptor_key: str) -> Resolution | None:
        """Rung one. A person's own decision beats everything, always.

        TODO: call ``find_override``; on a hit return a Resolution with
        ``rung="override"`` and no confidence, because a person's decision is
        not a probability.
        """
        raise NotImplementedError

    async def _alias(self, descriptor_key: str) -> Resolution | None:
        """Rung two.

        TODO: call ``find_alias``; on a hit return ``rung="alias"``. One
        indexed read, which is what the global uniqueness of ``descriptor_key``
        is for.
        """
        raise NotImplementedError

    async def _trgm(self, descriptor_key: str) -> Resolution | None:
        """Rung three. Trigram similarity above the threshold.

        TODO:
          1. Call ``find_similar`` with ``self._threshold``.
          2. Return ``rung="merchant_default"`` with the similarity score as
             ``confidence``, so a weak match can be routed to review.
          3. Tune the threshold against real descriptors. It is a real
             decision: too low and unrelated merchants merge, too high and the
             rung never fires and every near miss becomes a paid call.

        String similarity, deliberately, not semantic similarity.
        """
        raise NotImplementedError

    async def _llm(self, descriptor_keys: list[str]) -> dict[str, Resolution]:
        """Rung four. Only descriptors that reached here.

        TODO for step 5.5:
          1. Call ``self._llm.classify_batch``.
          2. Map suggestions onto resolutions with ``rung="llm"`` and the
             adapter's ``prompt_version``, which is what makes a later
             reprocess of model-derived rows a query rather than a guess.
          3. Let ``LLMUnavailable`` leave these descriptors unresolved. An
             outage degrades classification; it never fails an import.
          4. Accept a shorter result than the input. A descriptor the model
             declined is simply absent, and nothing is invented to pad it.

        Only the normalised strings cross the boundary. No amounts, no dates,
        no PDF, no identity, nothing that ties a merchant to a person.
        """
        raise NotImplementedError

    @staticmethod
    def _excluded(descriptor_key: str) -> bool:
        return is_person_transfer(descriptor_key)
