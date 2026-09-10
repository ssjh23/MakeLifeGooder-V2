"""Registration and sign-in.

Implemented, not stubbed: every other endpoint sits behind a session, so an
unwritten authenticator would leave the whole scaffold untestable.
"""

from __future__ import annotations

import uuid

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import Conflict, Unauthenticated
from app.api.security import hash_password, needs_rehash, verify_password
from app.db.repositories import UserRepository
from app.telemetry import events, get_logger

logger = get_logger(__name__)


class AuthService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._users = UserRepository(session)

    async def register(
        self, *, email: str, password: str, name: str | None
    ) -> uuid.UUID:
        existing = await self._users.get_by_email(email)
        if existing is not None:
            # A registration form legitimately has to say the address is taken,
            # so this leaks account existence and there is no way around it.
            # Password reset does not have that excuse, which is why it always
            # returns the same answer (TC-AUTH-007).
            raise Conflict("An account with that email already exists.")

        try:
            user = await self._users.create(
                email=email, password_hash=hash_password(password), display_name=name
            )
        except IntegrityError as exc:
            # Two simultaneous registrations for one address. The unique index
            # is the real guard; the check above is only a friendlier path to
            # the same answer.
            raise Conflict("An account with that email already exists.") from exc

        logger.info(events.AUTH_REGISTER_SUCCEEDED, user_id=str(user.id))
        return user.id

    async def authenticate(self, *, email: str, password: str) -> uuid.UUID:
        user = await self._users.get_by_email(email)
        password_hash = user.password_hash if user else None

        if not verify_password(password=password, password_hash=password_hash) or user is None:
            # One message for wrong password and unknown address, and both
            # paths do the same hashing work, so neither the wording nor the
            # timing distinguishes them (TC-AUTH-006).
            logger.info(events.AUTH_LOGIN_FAILED)
            raise Unauthenticated("Email or password is incorrect.")

        if user.password_hash and needs_rehash(user.password_hash):
            # Sign-in is the only moment the plaintext is available, so it is
            # the only moment a stored hash can be upgraded to current
            # parameters.
            await self._users.set_password_hash(user.id, hash_password(password))

        logger.info(events.AUTH_LOGIN_SUCCEEDED, user_id=str(user.id))
        return user.id

    async def me(self, user_id: uuid.UUID) -> tuple[uuid.UUID, str, str | None, bool]:
        user = await self._users.get(user_id)
        if user is None:
            raise Unauthenticated("No valid session.")
        has_statements = await self._users.has_committed_statements(user_id)
        return user.id, str(user.email), user.display_name, has_statements

    async def request_password_reset(self, email: str) -> None:
        """Always succeeds, whether or not the address is registered.

        DEFERRED. Endpoints are specified; no screen is drawn and no mail
        transport is chosen. Listed as an open question in User Flows, so do not
        invent the surface. Pick it up whenever email delivery is decided.

        TODO, when you do:
          1. Issue a single-use token with a short expiry.
          2. Send it, and return 202 either way with an identical body.
          3. Match the timing of the two paths as well as the body. Answering
             faster for an unknown address turns this into an account
             enumeration oracle just as surely as answering differently
             (TC-AUTH-007).
        """
        raise NotImplementedError("Password reset delivery is not wired up yet.")

    async def confirm_password_reset(self, *, token: str, new_password: str) -> None:
        """DEFERRED, with :meth:`request_password_reset`.

        TODO, when you do:
          1. Validate the token and reject an expired one.
          2. Set the new password with :func:`app.api.security.hash_password`.
          3. Consume the token so a second use fails (TC-AUTH-008).
          4. Decide whether existing sessions survive. They should not.
        """
        raise NotImplementedError("Password reset confirmation is not written yet.")
