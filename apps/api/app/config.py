"""Application configuration loaded from environment."""

from functools import lru_cache
from typing import Literal, Self

import structlog
from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = structlog.get_logger(__name__)

AppEnv = Literal["development", "staging", "production"]

# Marker for the unsigned dev default. Production startup must NOT see
# this value — settings validation raises if it does.
DEV_JWT_SECRET = "dev-only-not-for-prod-32-chars-min!"  # noqa: S105 — sentinel, not a credential

# HS256 best practice is ≥ 32 bytes of secret material so a brute-force
# adversary doesn't compress the keyspace.
_MIN_JWT_SECRET_BYTES = 32


class Settings(BaseSettings):
    """Runtime settings sourced from environment variables.

    Loaded once per process via `get_settings()`. Never log raw values
    of fields ending in `_key` or `_secret`.
    """

    app_env: AppEnv = "development"
    app_port: int = 8000
    app_log_level: str = "info"

    database_url: str = Field(
        default="postgresql+asyncpg://postgres:postgres@localhost:5432/careercoach"
    )
    redis_url: str = Field(default="redis://localhost:6379/0")
    qdrant_url: str = Field(default="http://localhost:6333")

    sentry_dsn: str = ""

    # `SecretStr` so `repr(settings)` / debug logging never leaks the
    # raw value the way a plain `str` field would. The dev default
    # `DEV_JWT_SECRET` is OK for `app_env == "development"` only —
    # the cross-field validator below raises if it leaks into staging
    # or production.
    jwt_secret: SecretStr = Field(default=SecretStr(DEV_JWT_SECRET))

    # Sessions + turns persistence backend.
    # `memory` (default) keeps everything dict-backed inside the worker
    # so dev runs without docker still work. `postgres` switches to the
    # SQLAlchemy-backed impls; alembic must be `upgrade head` first.
    sessions_repo_backend: Literal["memory", "postgres"] = Field(default="memory")

    # Auth user persistence — same defaulting rule.
    auth_repo_backend: Literal["memory", "postgres"] = Field(default="memory")

    # SMS code store — Redis is the right home (5-min TTL + atomic
    # GETDEL for one-shot verification). Defaults to in-memory so dev
    # runs without docker still work; production should set this to
    # `redis` so codes survive a restart and parallel verifies can't
    # replay the same code.
    auth_code_store_backend: Literal["memory", "redis"] = Field(default="memory")

    # Sharecards score-cache persistence. Set to `postgres` so a
    # restart doesn't drop the score-card render cache for previously
    # ended sessions (otherwise `/sharecards/session/{id}` 404s on
    # sessions that ended pre-restart even though the LLM summary
    # already cost money to produce).
    sharecards_score_repo_backend: Literal["memory", "postgres"] = Field(default="memory")

    # LLM · DeepSeek (primary, OpenAI-compatible).
    # Empty key means the adapter will refuse to send requests; only the
    # router knows how to fall back when this happens.
    deepseek_api_key: SecretStr = Field(default=SecretStr(""))
    deepseek_base_url: str = Field(default="https://api.deepseek.com")
    deepseek_model: str = Field(default="deepseek-chat")

    # LLM · Qwen / DashScope (backup, OpenAI-compatible mode).
    # DashScope hosts the OpenAI-compatible endpoint under
    # /compatible-mode/v1 — keep the trailing /compatible-mode here
    # since the adapter appends /v1/chat/completions.
    qwen_api_key: SecretStr = Field(default=SecretStr(""))
    qwen_base_url: str = Field(default="https://dashscope.aliyuncs.com/compatible-mode")
    qwen_model: str = Field(default="qwen-max")

    # Langfuse · LLM trace observability (foundation §3.7.1).
    # Empty keys mean trace is disabled — `get_langfuse_client` returns
    # None and callers skip instrumentation, so dev runs without a
    # Langfuse instance still work.
    langfuse_public_key: SecretStr = Field(default=SecretStr(""))
    langfuse_secret_key: SecretStr = Field(default=SecretStr(""))
    langfuse_host: str = Field(default="http://localhost:3001")

    # Aliyun Content Moderation 2.0 — primary moderation backend when keys
    # are configured. Empty AK/secret means we skip the cascade and the
    # moderation service falls back to the bundled local dict.
    aliyun_access_key_id: SecretStr = Field(default=SecretStr(""))
    aliyun_access_key_secret: SecretStr = Field(default=SecretStr(""))
    aliyun_moderation_endpoint: str = Field(
        default="green-cip.cn-shanghai.aliyuncs.com",
        description="Aliyun Green-CIP host. Region prefix follows aliyun pop names.",
    )
    aliyun_moderation_service: str = Field(
        default="chat_detection_pro",
        description="Aliyun service scene id — `chat_detection_pro` covers IM/UGC.",
    )
    aliyun_moderation_timeout_s: float = Field(
        default=0.8,
        description="Per-request budget. CascadingBackend falls back to local dict on timeout.",
    )

    # Share-card storage. `dir` is where the LocalFilesystemStorage drops
    # PNGs; `public_base_url` is the prefix the frontend hits to fetch
    # them. v0 dev runs both on the same FastAPI process; prod swaps
    # the storage backend for S3/OSS without changing the URL contract.
    sharecards_storage_dir: str = Field(
        default="./var/sharecards",
        description="Local dir for share-card PNGs. Created on startup.",
    )
    sharecards_public_base_url: str = Field(
        default="http://localhost:8000/static/sharecards",
        description="URL prefix returned in ShareCardResponse.png_url + share_links.save_local.",
    )
    sharecards_app_origin: str = Field(
        default="https://careercoach.app",
        description="Origin used inside QR codes + share-link deep paths (e.g. /share/{card_id}).",
    )

    model_config = SettingsConfigDict(
        env_file=("../../.env", ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    @model_validator(mode="after")
    def _validate_jwt_secret(self) -> Self:
        """Fail-fast on a startup-time misconfiguration of the signing
        secret. Catches three classes of mistake:

          1. Production / staging deploy that forgot to set
             `JWT_SECRET` — would otherwise sign tokens with the
             public dev default, letting anyone mint valid JWTs.
          2. Any environment with a too-short secret (HS256
             best-practice is ≥ 32 bytes).
          3. Dev runs with the canonical default — log a WARNING so
             nobody mistakes it for a real secret on their screen.
        """
        raw = self.jwt_secret.get_secret_value()

        if len(raw.encode("utf-8")) < _MIN_JWT_SECRET_BYTES:
            raise ValueError(
                f"jwt_secret must be at least {_MIN_JWT_SECRET_BYTES} bytes "
                f"(got {len(raw.encode('utf-8'))} bytes). HS256 keys shorter "
                "than 32 bytes weaken the signature meaningfully."
            )

        if raw == DEV_JWT_SECRET:
            if self.app_env != "development":
                raise ValueError(
                    "jwt_secret is set to the public dev default in a "
                    f"non-dev env (app_env={self.app_env}). Set the "
                    "JWT_SECRET environment variable to a unique value "
                    "≥ 32 bytes before starting the server."
                )
            # Dev: allowed, but make it visible so it never gets shipped
            # by accident.
            logger.warning(
                "jwt_secret_is_dev_default",
                msg="using DEV_JWT_SECRET; do NOT deploy with this value",
            )

        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
