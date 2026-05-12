"""Application configuration loaded from environment."""

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

AppEnv = Literal["development", "staging", "production"]


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

    jwt_secret: str = Field(default="dev-only-not-for-prod-32-chars-min!")

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

    model_config = SettingsConfigDict(
        env_file=("../../.env", ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
