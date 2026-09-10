"""The dedupe guard. GATE.

Three idempotency keys, all reachable in ordinary use rather than only under
failure. That is the test of whether an idempotency scheme is real: if a key
only matters when something crashes, it will be wrong when something crashes.

  file_sha256   the same file uploaded twice. People do this constantly.
  dedupe_hash   the same transaction appearing in two statements whose periods
                overlap. Banks do this at period boundaries.
  job id        a worker dying mid-stage and the job being retried.

The database enforces the first two with unique constraints, because TC-DUP-007
and TC-TDUP-008 insert duplicates in raw SQL with the service bypassed. Checks
in application code are a courtesy that produces a good error message; they are
not the guarantee.

===========================================================================
BUILD STEP 0.4   depends on: nothing   blocks: 3.2 transaction repo, 4.2 extract
Verify: uv run pytest tests/unit/extract/test_dedupe.py
===========================================================================
The last of the four gates that can be built with no database and no PDF.
"""

from __future__ import annotations

from app.extract.base import ParsedRow


class DedupeGuard:
    """Idempotency for statements and rows."""

    def statement_hash(self, pdf_bytes: bytes) -> bytes:
        """SHA-256 of the file as uploaded.

        Of the bytes, not of any parsed representation: the point is to answer
        "have I seen this exact document" before spending anything on parsing
        it (TC-DUP-002).

        TODO:
          1. Hash the raw bytes with SHA-256.
          2. Return ``digest()``, not ``hexdigest()``. The column is
             ``bytea(32)`` and the test checks the length.
        """
        raise NotImplementedError

    def row_hash(self, row: ParsedRow, *, account_id: str) -> bytes:
        """A stable identity for one transaction.

        The design question is which fields to include, and it has real
        consequences in both directions.

        Too few, and two genuine purchases collide: the same coffee, the same
        price, the same shop, the same day is an ordinary thing to do, and
        silently dropping the second one loses a real transaction.

        Too many, and the same row appearing in two overlapping statements
        hashes differently and is imported twice, which is the case this exists
        to catch.

        TODO:
          1. Build a delimited string from: ``account_id``, ``posted_on``,
             ``description_raw`` and ``amount_minor``.
          2. Use ``description_raw``, **not** the normalised key. Two outlets of
             one chain on one day at one price are two purchases, and hashing
             the key would merge them and lose one.
          3. Include ``account_id``. Scoping only to the user would drop a
             genuine charge that appears on two of a person's own cards.
          4. Pick a delimiter that cannot appear in a descriptor, or
             ``"AB" + "C"`` and ``"A" + "BC"`` collide.
          5. Return 32 raw bytes.

        Note the asymmetry with duplicate *review* (screen 05): that flags
        same-merchant, same-amount, same-day pairs for a person to judge,
        because it cannot know. This is for rows that are provably the same
        record seen twice, which is not a judgment call and is skipped silently
        (TC-TDUP-006).
        """
        raise NotImplementedError

    def filter_known(self, rows: list[ParsedRow], known_hashes: set[bytes]) -> list[ParsedRow]:
        """Drop rows already present under another statement.

        TODO:
          1. Keep every row whose :meth:`row_hash` is not in ``known_hashes``.
          2. Do **not** deduplicate within ``rows`` itself. Two identical rows
             on one statement are a real double charge, and screen 05 asks the
             user which it was. This function only removes rows already imported
             from a different statement.
        """
        raise NotImplementedError
