"""Passwords and session cookies.

Written rather than stubbed. Everything else in the system is reachable only
after signing in, so an unwritten authenticator would mean an untestable
scaffold, and a half-written one is a vulnerability that looks finished.

Two choices worth recording.

**Argon2id, not bcrypt.** Memory-hard, so a GPU gains far less against it, and
it has no silent input truncation. The parameters below are the reference
moderate settings; raise them if sign-in is comfortably fast on the deployed
box.

**Signed cookies, not server-side sessions.** ADR-008 chose short-lived signed
cookies precisely so that no session store is needed, which is one of the two
reasons Redis fell out of the launch architecture. The cost is a revocation
gap: a stolen cookie stays valid until it expires. That is bounded by keeping
the lifetime short and is the accepted trade.
"""

from __future__ import annotations

import uuid

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

_hasher = PasswordHasher(time_cost=3, memory_cost=64 * 1024, parallelism=4)

SESSION_SALT = "ledger.session.v1"


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(*, password: str, password_hash: str | None) -> bool:
    """Check a password against a stored hash.

    A user with no hash, meaning an OIDC-only account, and a user that does not
    exist at all both take the same path and cost roughly the same time. The
    error message at the call site does not distinguish wrong-password from
    unknown-email either (TC-AUTH-006): telling an attacker which half was
    wrong turns a login form into an account enumeration oracle.
    """
    if not password_hash:
        # Still perform a hash so a missing account is not measurably faster
        # than a wrong password.
        _hasher.hash(password)
        return False
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    """True when the stored hash used weaker parameters than current policy."""
    return _hasher.check_needs_rehash(password_hash)


class SessionCodec:
    """Serialises the session cookie."""

    def __init__(self, *, secret: str, ttl_seconds: int) -> None:
        self._serializer = URLSafeTimedSerializer(secret, salt=SESSION_SALT)
        self._ttl = ttl_seconds

    def issue(self, user_id: uuid.UUID) -> str:
        return self._serializer.dumps({"sub": str(user_id)})

    def read(self, token: str) -> uuid.UUID | None:
        """Return the user id, or ``None`` for anything not currently valid.

        Expiry and tampering collapse to the same answer on purpose. Both mean
        "no valid session", and the caller has no use for the difference.
        """
        try:
            payload = self._serializer.loads(token, max_age=self._ttl)
        except (BadSignature, SignatureExpired):
            return None
        try:
            return uuid.UUID(payload["sub"])
        except (KeyError, TypeError, ValueError):
            return None
