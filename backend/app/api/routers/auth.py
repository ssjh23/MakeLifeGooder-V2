"""Authentication. Screens 00 and 01, plus the OIDC seam.

Both password sign-in and OIDC (Auth0) are implemented, and they end at the
same place on purpose: the cookie, the session codec and every downstream
dependency are identical either way, so ADR-008's OIDC choice never became a
second auth system to keep in sync with the first. When Auth0 is not
configured (`Settings.oidc_configured`, false by default), the OIDC routes
answer with a clear 503 rather than crashing -- that is what keeps the whole
suite and local dev running with no external identity provider (see
`app/api/oidc.py`).
"""

from __future__ import annotations

from typing import Annotated

from auth0_server_python.auth_server.server_client import ServerClient
from auth0_server_python.error import Auth0Error
from fastapi import APIRouter, Depends, Request, Response, status
from starlette.responses import RedirectResponse

from app.api.deps import CurrentUser, SettingsDep, UnscopedSession, get_session_codec
from app.api.errors import OidcUpstreamError, Unauthenticated
from app.api.oidc import get_oidc_client
from app.api.security import SessionCodec
from app.schemas.auth import (
    LoginRequest,
    MeResponse,
    PasswordResetConfirm,
    PasswordResetRequest,
    RegisterRequest,
)
from app.services.auth import AuthService
from app.telemetry import get_logger

logger = get_logger(__name__)

router = APIRouter(tags=["auth"])

CodecDep = Annotated[SessionCodec, Depends(get_session_codec)]
OidcClientDep = Annotated[ServerClient, Depends(get_oidc_client)]


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
# issued by the same codec. See app/api/oidc.py for the SDK integration.


@router.get("/auth/oidc/start")
async def oidc_start(request: Request, client: OidcClientDep) -> Response:
    """Redirect to Auth0. A plain browser navigation, not a fetch target --
    the frontend links here directly rather than calling it as an API.

    `client` raises `OidcNotConfigured` (503) before this body runs at all
    when no Auth0 application is set up (`app/api/oidc.py`); that is the
    default in local dev and in the test suite.
    """
    # A scratch response so the transaction store has somewhere to write the
    # PKCE cookie; its Set-Cookie header is copied onto the real redirect
    # below; nothing else about it is ever sent.
    scratch = Response()
    try:
        authorize_url = await client.start_interactive_login(
            store_options={"request": request, "response": scratch}
        )
    except Auth0Error as exc:
        logger.warning("oidc.start_failed", error=str(exc))
        raise OidcUpstreamError("Could not reach Auth0 to start sign-in.") from exc

    redirect = RedirectResponse(authorize_url)
    for cookie in scratch.headers.getlist("set-cookie"):
        redirect.headers.append("set-cookie", cookie)
    return redirect


@router.get("/auth/oidc/callback")
async def oidc_callback(
    request: Request,
    session: UnscopedSession,
    codec: CodecDep,
    settings: SettingsDep,
    client: OidcClientDep,
) -> Response:
    """Exchange the authorization code, resolve the local account, and hand
    back the same session cookie password sign-in issues."""
    scratch = Response()
    try:
        result = await client.complete_interactive_login(
            str(request.url), store_options={"request": request, "response": scratch}
        )
    except Auth0Error as exc:
        logger.warning("oidc.callback_failed", error=str(exc))
        raise Unauthenticated("Sign-in with Auth0 failed.") from exc

    # Enterprise Connect mode (app/api/oidc.py): no session, no refresh token,
    # just the verified claims from the ID token or userinfo.
    claims = result["user"]
    user_id = await AuthService(session).login_with_oidc(
        subject=claims["sub"],
        email=claims.get("email"),
        email_verified=bool(claims.get("email_verified")),
        name=claims.get("name"),
    )

    # Back to the SPA, not this API -- the first configured origin is the one
    # the browser can actually load (see Settings.cors_origins).
    redirect = RedirectResponse(f"{settings.cors_origins[0]}/")
    for cookie in scratch.headers.getlist("set-cookie"):
        redirect.headers.append("set-cookie", cookie)
    _set_session_cookie(redirect, token=codec.issue(user_id), settings=settings)
    return redirect
