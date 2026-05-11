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

    model_config = SettingsConfigDict(
        env_file=("../../.env", ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
