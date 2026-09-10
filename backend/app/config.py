"""Application settings.

This module is the seam that absorbs the difference between Docker on a laptop
and AWS in Singapore. Endpoint addresses, credentials, TLS, allowed origins and
which adapters are live are all environment. Application code never branches on
where it is running.

The exceptions are documented against the individual fields below, and each one
is a case where a convenient local setup would make the test suite lie.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, PostgresDsn, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Environment = Literal["local", "test", "staging", "production"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    environment: Environment = "local"
    log_level: str = "INFO"
    cors_allowed_origins: str = "http://localhost:5173"

    # -- Database ----------------------------------------------------------
    #
    # Four URLs. The differences between them are load bearing, not tidiness.

    #: Through pgbouncer in transaction mode, as ``ledger_app``. Transaction
    #: pooling is what stops ``SET LOCAL app.user_id`` outliving its
    #: transaction, and ``ledger_app`` owns no tables, so row level security
    #: actually applies to it. Both halves are required; either one alone
    #: gives isolation that looks present and is not.
    database_url: PostgresDsn

    #: Direct connection as the owning role. Alembic only. The application must
    #: never use this: a table's owner bypasses RLS entirely.
    database_migrator_url: PostgresDsn

    #: Direct connection, bypassing the pooler. procrastinate uses
    #: LISTEN/NOTIFY for immediate dispatch and that does not survive
    #: transaction-mode pooling, so the worker talks to Postgres itself.
    database_worker_url: PostgresDsn

    #: Through pgbouncer in *session* mode. Used by one test, which exists to
    #: demonstrate the hazard ADR-008 forbids. Nothing else may read this.
    database_session_mode_url: PostgresDsn | None = None

    db_pool_size: int = 10
    db_max_overflow: int = 5

    # -- Object storage ----------------------------------------------------

    #: Where *this process* reaches storage.
    s3_endpoint: str | None = None

    #: Where the *browser* reaches storage, and therefore the only endpoint
    #: used when signing. A presigned URL's signature covers the host, so a URL
    #: signed for an address the browser cannot resolve is unusable and cannot
    #: be repaired by rewriting it. On a host-run API both values are equal; in
    #: compose they differ; in AWS both are unset and boto3 derives them.
    s3_public_endpoint: str | None = None

    s3_region: str = "ap-southeast-1"
    s3_bucket: str = "ledger-statements"
    s3_access_key_id: SecretStr | None = None
    s3_secret_access_key: SecretStr | None = None

    #: MinIO requires it and AWS accepts it, so this stays true in both places
    #: rather than becoming a branch.
    s3_force_path_style: bool = True

    #: Sent locally as well as in production, so the production code path is
    #: exercised before deployment rather than during it (TC-SEC-008).
    s3_server_side_encryption: str | None = "AES256"
    s3_presign_expiry_seconds: int = 900

    # -- Sessions ----------------------------------------------------------

    session_secret: SecretStr = SecretStr("dev-only-not-a-secret-change-me")
    session_ttl_seconds: int = 3600
    session_cookie_name: str = "ledger_session"
    session_cookie_secure: bool = False
    password_min_length: int = 12

    oidc_issuer: str | None = None
    oidc_client_id: str | None = None
    oidc_client_secret: SecretStr | None = None

    # -- LLM ---------------------------------------------------------------

    #: "fake" is deterministic and offline, and is the default everywhere except
    #: production, so no API key is needed to run or test anything.
    llm_adapter: Literal["fake", "claude"] = "fake"
    #: Defaults to the most capable model. Descriptor classification is cheap
    #: per call and rare per merchant, so the bill is driven by how many new
    #: merchants appear rather than by the model, and accuracy here compounds:
    #: a wrong alias is cached and then reused for every tenant. Moving down to
    #: claude-sonnet-5 or claude-haiku-4-5 is a deliberate cost decision, which
    #: is why it is configuration rather than a default chosen for you.
    llm_model: str = "claude-opus-5"
    llm_batch_size: int = 50
    llm_prompt_version: str = "v1"
    anthropic_api_key: SecretStr | None = None

    # -- Classification ----------------------------------------------------

    trgm_similarity_threshold: float = 0.4

    # -- Telemetry ---------------------------------------------------------

    #: Salt for ``descriptor_key_hash``. Rotating it breaks correlation between
    #: old and new log lines, which is the intended trade-off for a secret that
    #: has leaked.
    log_hash_salt: SecretStr = SecretStr("dev-only-log-salt")

    @field_validator("cors_allowed_origins")
    @classmethod
    def _reject_wildcard_in_production(cls, value: str) -> str:
        if value.strip() == "*":
            raise ValueError(
                "A wildcard origin cannot be used with credentialed requests. "
                "List the SPA origin explicitly."
            )
        return value

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.cors_allowed_origins.split(",") if o.strip()]

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def signing_endpoint(self) -> str | None:
        """The endpoint used to sign presigned URLs.

        Falls back to the internal endpoint so a single-endpoint setup needs no
        extra configuration, but never the other way round: signing with an
        internal address produces URLs the browser cannot use.
        """
        return self.s3_public_endpoint or self.s3_endpoint


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]


settings_dependency = get_settings
