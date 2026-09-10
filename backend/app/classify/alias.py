"""Writing the shared alias cache.

``merchant_aliases`` is cross-tenant on purpose, and it is the single decision
that makes the model bill flatten instead of scaling with usage. Once anyone's
statement teaches the system that ``fairprice`` is a supermarket, nobody pays
for that merchant again.

``source`` exists so a bad batch can be purged without touching verified work.
If a prompt change turns out to have produced poor categories, the rows it
wrote are identifiable and removable; without that column the only options are
trusting them or discarding the whole table.

===========================================================================
BUILD STEP 5.2   depends on: 5.1 merchant repository
Verify: uv run pytest tests/integration/test_cascade.py
===========================================================================
Small, but write the cross-tenant test with it: user A classifies FAIRPRICE,
user B imports a statement containing it, and B's import makes no model call
(TC-REV-010). That test is the evidence for the cost claim in the capacity
model, and it is worth having early.
"""

from __future__ import annotations

import uuid

from app.db.repositories import MerchantRepository


class AliasWriter:
    def __init__(self, merchants: MerchantRepository) -> None:
        self._merchants = merchants

    async def upsert(
        self,
        *,
        descriptor_key: str,
        merchant_id: uuid.UUID,
        source: str,
        confidence: float | None,
    ) -> None:
        """Record a descriptor to merchant mapping for everyone.

        TODO:
          1. Delegate to ``MerchantRepository.upsert_alias``.
          2. Refuse to write an empty or whitespace-only ``descriptor_key``.
          3. Assert the transfer filter ran. This is the table that would carry
             a private name into every other tenant's classification path, and
             it is the last point at which that can be stopped.
          4. Decide what happens to a low-confidence model guess, and make the
             decision explicit. It becomes the answer for every future tenant
             who sees that descriptor, so "write it and hope" is a choice about
             other people's data. Options worth weighing: do not write below a
             threshold, or write it and mark it for later review.
          5. Set ``source`` honestly: ``llm``, ``rule`` or ``manual``. It is
             what makes a bad batch purgeable.

        A user correcting a category writes a ``merchant_overrides`` row, not
        an alias: their opinion is theirs. Only a correction confident enough to
        be right for strangers belongs here.
        """
        raise NotImplementedError
