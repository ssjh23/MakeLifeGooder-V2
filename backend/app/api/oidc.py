"""The OIDC seam (ADR-008), filled in for Auth0.

`auth0-server-python` drives the Authorization Code + PKCE exchange. It runs
here in the SDK's *Enterprise Connect* mode: Auth0 is a pure identity relay,
the SDK persists no session of its own and issues no refresh token, and
`complete_interactive_login` hands back verified claims and nothing else (see
`ServerClient._complete_enterprise_login` in the installed package). That is
exactly ADR-008's shape -- "both handlers end in the same place as password
sign-in: a signed cookie issued by the same codec" -- because with Enterprise
Connect there is no second session to keep in sync with the first. It is also
why this module never constructs a `state_store`: with `enterprise_connect=
True` the SDK never calls one, so the constructor's in-memory default is
simply unused rather than silently wrong.

The transaction store still has to be real: it carries the PKCE
`code_verifier` and this app's `redirect_uri`/`app_state` across the redirect
to Auth0 and back. `AbstractDataStore.encrypt`/`decrypt` fold the store
`identifier` (which embeds the OAuth `state` value -- see
`ServerClient.start_interactive_login`/`complete_interactive_login`) into the
JWE key derivation, so a callback whose `state` does not match the
transaction stored under it fails to *decrypt*, not just fails an `if`
check. `CookieTransactionStore` below must always pass `identifier` through
to `encrypt`/`decrypt` unchanged for that guarantee to hold -- that is the
CSRF protection this scheme relies on, on top of PKCE.

Cookie-backed rather than a database table or Redis: the transaction is
single-use and lives for minutes, so the OIDC handshake needs no shared
storage across requests or workers, the same reasoning `SessionCodec` already
uses for the app session cookie.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from auth0_server_python.auth_server.server_client import ServerClient
from auth0_server_python.auth_types import TransactionData
from auth0_server_python.store import TransactionStore
from fastapi import Request, Response

from app.api.errors import OidcNotConfigured
from app.config import Settings, get_settings

TRANSACTION_COOKIE_NAME = "ledger_oidc_txn"
#: The whole round trip -- redirect to Auth0, sign in there, redirect back --
#: has to finish inside this window. Unrelated to the app session's lifetime;
#: this only needs to outlive one login attempt.
TRANSACTION_TTL_SECONDS = 600
#: Scoped to the OIDC routes only. The app session cookie (SessionCodec) is
#: the one sent on every other request; this one has no business leaving the
#: three requests that make up a single login attempt.
TRANSACTION_COOKIE_PATH = "/api/v1/auth/oidc"


# auth0-server-python ships no py.typed marker (see the mypy override in
# pyproject.toml), so mypy sees TransactionStore as Any and flags subclassing
# it under --strict. The base class is real at runtime.
class CookieTransactionStore(TransactionStore):  # type: ignore[misc]
    """Carries one in-flight OIDC transaction in a short-lived signed cookie.

    One cookie, one transaction, at a time: this app never has two concurrent
    Auth0 logins in flight in the same browser, so there is no need to key
    more than one transaction per cookie jar.
    """

    def __init__(self, *, secret: str, cookie_secure: bool) -> None:
        super().__init__({"secret": secret})
        self._cookie_secure = cookie_secure

    async def set(
        self,
        identifier: str,
        state: Any,
        remove_if_expires: bool = False,
        options: dict[str, Any] | None = None,
    ) -> None:
        assert options is not None  # always called with {"request", "response"} here
        response: Response = options["response"]
        payload = state.dict() if hasattr(state, "dict") else dict(state)
        encrypted = self.encrypt(identifier, payload)
        response.set_cookie(
            TRANSACTION_COOKIE_NAME,
            encrypted,
            max_age=TRANSACTION_TTL_SECONDS,
            httponly=True,
            secure=self._cookie_secure,
            samesite="lax",
            path=TRANSACTION_COOKIE_PATH,
        )

    async def get(
        self, identifier: str, options: dict[str, Any] | None = None
    ) -> TransactionData | None:
        assert options is not None
        request: Request = options["request"]
        raw = request.cookies.get(TRANSACTION_COOKIE_NAME)
        if raw is None:
            return None
        try:
            decrypted = self.decrypt(identifier, raw)
        except Exception:
            # Wrong `state`, expired secret rotation, or tampering -- all the
            # same answer as "no transaction found" to the caller, which
            # raises MissingTransactionError either way.
            return None
        return TransactionData(**decrypted) if isinstance(decrypted, dict) else decrypted

    async def delete(self, identifier: str, options: dict[str, Any] | None = None) -> None:
        assert options is not None
        response: Response = options["response"]
        response.delete_cookie(TRANSACTION_COOKIE_NAME, path=TRANSACTION_COOKIE_PATH)


def _build_oidc_client(settings: Settings) -> ServerClient:
    assert settings.auth0_domain and settings.auth0_client_id and settings.auth0_client_secret
    return ServerClient(
        domain=settings.auth0_domain,
        client_id=settings.auth0_client_id,
        client_secret=settings.auth0_client_secret.get_secret_value(),
        # Reusing the app session secret rather than adding a second one: both
        # protect data this process alone reads back within minutes, and
        # `SessionCodec` already documents the rotation trade-off for it.
        secret=settings.session_secret.get_secret_value(),
        redirect_uri=settings.auth0_redirect_uri,
        transaction_store=CookieTransactionStore(
            secret=settings.session_secret.get_secret_value(),
            cookie_secure=settings.session_cookie_secure,
        ),
        authorization_params={"scope": "openid profile email"},
        # See module docstring: Auth0 is a pure identity relay here, so the
        # SDK is told not to keep a session of its own.
        enterprise_connect=True,
    )


@lru_cache
def _cached_client() -> ServerClient:
    """One client for the process's life, mirroring `get_session_codec`.

    Rebuilding this per request would also throw away the SDK's in-instance
    cache of Auth0's OIDC metadata, turning every login step into an extra
    round trip to Auth0's discovery document. Only reached once
    `get_oidc_client` has confirmed Auth0 is actually configured.
    """
    return _build_oidc_client(get_settings())


def get_oidc_client() -> ServerClient:
    """FastAPI dependency for the shared Auth0 client.

    Checks `settings.oidc_configured` on every call -- not itself cached, so
    an unconfigured environment (every local dev box and the test suite, by
    design -- see ADR-008) gets `OidcNotConfigured` on every attempt rather
    than caching a failure. `functools.lru_cache` never caches a raised
    exception, so this stays cheap regardless.
    """
    if not get_settings().oidc_configured:
        raise OidcNotConfigured(
            "Sign-in with Auth0 is not configured. Set AUTH0_DOMAIN, "
            "AUTH0_CLIENT_ID and AUTH0_CLIENT_SECRET."
        )
    return _cached_client()
