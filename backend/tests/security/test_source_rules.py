"""Rules the source has to keep. GREEN.

Two guarantees in this system are properties of how the code is written rather
than of what it does at runtime, so a runtime test cannot catch a regression.
Scanning the source is not elegant, and it is the only thing that works here.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[2] / "app"

pytestmark = pytest.mark.security


def _python_files() -> list[Path]:
    return sorted(APP.rglob("*.py"))


@pytest.mark.p0
def test_tenant_identity_is_never_set_without_local() -> None:
    """``SET LOCAL``, never a bare ``SET``, for ``app.user_id``.

    Under transaction pooling pgbouncer does not run a reset query, so a
    session-level ``SET`` can persist on a backend that is then handed to a
    different client. The value would be another user's identity, and every
    policy resolves against it.

    ``set_config(..., true)`` is the parameterised form and is what the codebase
    uses. This test exists so that a later "quick fix" using an f-string cannot
    land unnoticed.
    """
    offenders = []
    pattern = re.compile(r"\bSET\s+app\.user_id", re.IGNORECASE)
    for path in _python_files():
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if pattern.search(line) and "SET LOCAL" not in line.upper():
                offenders.append(f"{path.relative_to(APP.parent)}:{number}: {line.strip()}")
    assert not offenders, (
        "Tenant identity must be set with set_config(..., true) or SET LOCAL:\n"
        + "\n".join(offenders)
    )


@pytest.mark.p0
def test_tenant_identity_is_never_interpolated_into_sql() -> None:
    """The user id comes from a session cookie.

    ``SET LOCAL`` does not accept bind parameters, so the obvious way to write
    it is an f-string, and that turns a cookie into SQL. ``set_config`` takes a
    parameter, which is the reason the codebase issues a SELECT to do it.
    """
    offenders = []
    pattern = re.compile(r"""f["'].*app\.user_id.*\{""")
    for path in _python_files():
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if pattern.search(line):
                offenders.append(f"{path.relative_to(APP.parent)}:{number}")
    assert not offenders, "Tenant identity interpolated into SQL:\n" + "\n".join(offenders)


@pytest.mark.p0
def test_application_never_uses_the_migrator_connection() -> None:
    """Only Alembic may connect as the owning role.

    A table's owner is exempt from row level security, so an application module
    reaching for this URL would remove tenant isolation while every test kept
    passing.
    """
    offenders = []
    for path in _python_files():
        if path.name in {"config.py", "session.py"}:
            continue  # where the setting is legitimately declared
        text = path.read_text(encoding="utf-8")
        if "database_migrator_url" in text or "DATABASE_MIGRATOR_URL" in text:
            offenders.append(str(path.relative_to(APP.parent)))
    assert not offenders, (
        "The migrator connection is for Alembic only:\n" + "\n".join(offenders)
    )
