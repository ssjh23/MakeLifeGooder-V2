"""The dedupe guard. RED until app/extract/dedupe.py is written.

Pure functions over bytes and rows, so this runs without a database, without a
PDF and without the parser. It is the last of the four gates that can be built
in isolation.

The two directions of failure are both real and both bad, which is why the
cases below come in pairs:

  collides too easily   two genuine purchases become one, and a real
                        transaction disappears from the ledger
  collides too rarely   the same row in two overlapping statements is imported
                        twice, which is the case this exists to catch
"""

from __future__ import annotations

from datetime import date

import pytest

from app.extract.base import ParsedRow
from app.extract.dedupe import DedupeGuard

pytestmark = pytest.mark.unwritten

ACCOUNT = "11111111-1111-1111-1111-111111111111"
OTHER_ACCOUNT = "22222222-2222-2222-2222-222222222222"


def _row(
    *,
    day: int = 22,
    description: str = "MCDONALDS-JUNCTION8",
    amount: int = 1240,
) -> ParsedRow:
    return ParsedRow(
        posted_on=date(2026, 7, day),
        description_raw=description,
        amount_minor=amount,
    )


@pytest.fixture
def guard() -> DedupeGuard:
    return DedupeGuard()


class TestStatementHash:
    @pytest.mark.p0
    def test_TC_DUP_002_same_bytes_hash_the_same(self, guard: DedupeGuard) -> None:
        """Answers "have I seen this document" before anything is spent parsing
        it."""
        assert guard.statement_hash(b"%PDF-1.7 same") == guard.statement_hash(b"%PDF-1.7 same")

    @pytest.mark.p0
    def test_different_bytes_hash_differently(self, guard: DedupeGuard) -> None:
        assert guard.statement_hash(b"%PDF-1.7 a") != guard.statement_hash(b"%PDF-1.7 b")

    def test_hash_fits_the_column(self, guard: DedupeGuard) -> None:
        """statements.file_sha256 is bytea(32)."""
        assert len(guard.statement_hash(b"anything")) == 32


class TestRowHash:
    @pytest.mark.p0
    def test_TC_TDUP_006_identical_row_hashes_the_same(self, guard: DedupeGuard) -> None:
        """The case this exists for.

        A billing period boundary puts the same transaction on two consecutive
        statements. It must be written once, with no prompt, because this is
        not a judgment call.
        """
        assert guard.row_hash(_row(), account_id=ACCOUNT) == guard.row_hash(
            _row(), account_id=ACCOUNT
        )

    @pytest.mark.p0
    def test_different_amount_is_a_different_row(self, guard: DedupeGuard) -> None:
        a = guard.row_hash(_row(amount=1240), account_id=ACCOUNT)
        b = guard.row_hash(_row(amount=1250), account_id=ACCOUNT)
        assert a != b

    @pytest.mark.p0
    def test_different_date_is_a_different_row(self, guard: DedupeGuard) -> None:
        a = guard.row_hash(_row(day=22), account_id=ACCOUNT)
        b = guard.row_hash(_row(day=23), account_id=ACCOUNT)
        assert a != b

    @pytest.mark.p0
    def test_different_description_is_a_different_row(self, guard: DedupeGuard) -> None:
        a = guard.row_hash(_row(description="MCDONALDS-JUNCTION8"), account_id=ACCOUNT)
        b = guard.row_hash(_row(description="FAIRPRICE FINEST NEX"), account_id=ACCOUNT)
        assert a != b

    @pytest.mark.p0
    def test_TC_TDUP_009_same_row_on_two_accounts_does_not_collide(
        self, guard: DedupeGuard
    ) -> None:
        """Two of one person's own cards can carry the same charge.

        Scoping only to the user would silently drop the second one, which is a
        real transaction on a real card.
        """
        a = guard.row_hash(_row(), account_id=ACCOUNT)
        b = guard.row_hash(_row(), account_id=OTHER_ACCOUNT)
        assert a != b

    @pytest.mark.p0
    def test_the_raw_description_is_used_not_the_normalised_key(
        self, guard: DedupeGuard
    ) -> None:
        """Two outlets of one chain, same day, same price, are two purchases.

        Hashing the normalised key would merge them and lose one. Dedupe works
        on the source text; only classification works on the key.
        """
        a = guard.row_hash(_row(description="MCDONALDS-JUNCTION8"), account_id=ACCOUNT)
        b = guard.row_hash(_row(description="MCDONALDS (CCP)"), account_id=ACCOUNT)
        assert a != b

    def test_hash_fits_the_column(self, guard: DedupeGuard) -> None:
        """transactions.dedupe_hash is bytea(32)."""
        assert len(guard.row_hash(_row(), account_id=ACCOUNT)) == 32


class TestFilterKnown:
    @pytest.mark.p0
    def test_known_rows_are_dropped(self, guard: DedupeGuard) -> None:
        seen = _row(day=22)
        fresh = _row(day=23)
        known = {guard.row_hash(seen, account_id=ACCOUNT)}
        kept = guard.filter_known([seen, fresh], known, account_id=ACCOUNT)
        assert kept == [fresh]

    def test_nothing_known_keeps_everything(self, guard: DedupeGuard) -> None:
        rows = [_row(day=22), _row(day=23)]
        assert guard.filter_known(rows, set(), account_id=ACCOUNT) == rows

    @pytest.mark.p0
    def test_two_identical_rows_within_one_statement_are_both_kept(
        self, guard: DedupeGuard
    ) -> None:
        """A real double charge on one statement is not a dedupe case.

        Both rows are written, and screen 05 asks the user which it was. This
        function only removes rows already imported from another statement.
        """
        rows = [_row(), _row()]
        assert len(guard.filter_known(rows, set(), account_id=ACCOUNT)) == 2

    @pytest.mark.p0
    def test_a_row_known_on_another_account_is_not_dropped(
        self, guard: DedupeGuard
    ) -> None:
        """``known_hashes`` is scoped to one account.

        The same row hashed under a different account must not suppress it
        here, or a genuine charge on a second card silently disappears.
        """
        row = _row()
        known = {guard.row_hash(row, account_id=OTHER_ACCOUNT)}
        assert guard.filter_known([row], known, account_id=ACCOUNT) == [row]
