"""Authentication. Screens 00 and 01, plus the OIDC seam.

Password sign-in is implemented; OIDC is stubbed behind the same session code
path. ADR-008 chose OIDC, and this is not a departure from it: the cookie, the
session codec and every downstream dependency are identical either way, so
adding a provider means filling in two handlers rather than reworking the
system. It also means the whole suite runs with no external identity provider,
which is what keeps the local environment self-contained.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Response, status

from app.api.deps import CurrentUser, SettingsDep, UnscopedSession, get_session_codec
from app.api.security import SessionCodec
from app.schemas.auth import (
    LoginRequest,
    MeResponse,
    PasswordResetConfirm,
    PasswordResetRequest,
    RegisterRequest,
)
from app.services.auth import AuthService

router = APIRouter(tags=["auth"])

CodecDep = Annotated[SessionCodec, Depends(get_session_codec)]


def _set_session_cookie(
    response: Response, *, token: str, settings: SettingsDep
) -> None:
    response.set_cookie(
        key=settings.session_cookie_name,
        value=token,
        max_age=settings.session_ttl_seconds,
        # No JavaScript access, so an XSS bug cannot read the session.
        httponly=True,
        # HTTPS only outside local development.
        secure=settings.session_cookie_secure,
        # Lax rather than strict: strict would drop the cookie on a link
        # followed in from an email, which breaks password reset without
        # improving anything meaningful here.
        samesite="lax",
        path="/",
    )


@router.post("/auth/register", status_code=status.HTTP_201_CREATED, response_model=MeResponse)
async def register(
    payload: RegisterRequest,
    session: UnscopedSession,
    codec: CodecDep,
    settings: SettingsDep,
    response: Response,
) -> MeResponse:
    service = AuthService(session)
    user_id = await service.register(
        email=str(payload.email),
        password=payload.password.get_secret_value(),
        name=payload.name,
    )
    _set_session_cookie(response, token=codec.issue(user_id), settings=settings)
    return MeResponse(
        id=user_id, email=payload.email, name=payload.name, has_statements=False
    )


@router.post("/auth/login", response_model=MeResponse)
async def login(
    payload: LoginRequest,
    session: UnscopedSession,
    codec: CodecDep,
    settings: SettingsDep,
    response: Response,
) -> MeResponse:
    service = AuthService(session)
    user_id = await service.authenticate(
        email=str(payload.email), password=payload.password.get_secret_value()
    )
    _set_session_cookie(response, token=codec.issue(user_id), settings=settings)
    user_id, email, name, has_statements = await service.me(user_id)
    return MeResponse(id=user_id, email=email, name=name, has_statements=has_statements)


@router.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(response: Response, settings: SettingsDep) -> None:
    response.delete_cookie(settings.session_cookie_name, path="/")


@router.get("/me", response_model=MeResponse)
async def me(user_id: CurrentUser, session: UnscopedSession) -> MeResponse:
    resolved, email, name, has_statements = await AuthService(session).me(user_id)
    return MeResponse(id=resolved, email=email, name=name, has_statements=has_statements)


@router.post("/auth/password-reset/request", status_code=status.HTTP_202_ACCEPTED)
async def request_password_reset(
    payload: PasswordResetRequest, session: UnscopedSession
) -> dict[str, str]:
    """Always 202, with an identical body and comparable timing whether or not
    the address is registered. Answering differently turns a reset form into an
    account enumeration oracle (TC-AUTH-007)."""
    await AuthService(session).request_password_reset(str(payload.email))
    return {"status": "accepted"}


@router.post("/auth/password-reset/confirm", status_code=status.HTTP_204_NO_CONTENT)
async def confirm_password_reset(
    payload: PasswordResetConfirm, session: UnscopedSession
) -> None:
    await AuthService(session).confirm_password_reset(
        token=payload.token, new_password=payload.new_password.get_secret_value()
    )


# -- OIDC seam --------------------------------------------------------------
# Both handlers end in the same place as password sign-in: a signed cookie
# issued by the same codec. Filling them in touches this module and the
# settings, and nothing downstream.


@router.get("/auth/oidc/start")
async def oidc_start(settings: SettingsDep) -> dict[str, str]:
    raise NotImplementedError("No OIDC provider is configured yet (ADR-008).")


@router.get("/auth/oidc/callback")
async def oidc_callback(settings: SettingsDep) -> dict[str, str]:
    raise NotImplementedError("No OIDC provider is configured yet (ADR-008).")
