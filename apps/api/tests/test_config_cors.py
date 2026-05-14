"""Settings tests for the CORS allowlist parser.

`cors_allowed_origins` is a comma-separated string at the env layer
because pydantic-settings v2 still trips on JSON-list parsing from
`.env` files. The `.cors_allowed_origins_list` property does the
split; the contract test below pins the parsing so a future refactor
to JSON-list isn't a silent regression.
"""

from __future__ import annotations

from app.config import Settings


def test_default_origins_include_dev_frontends() -> None:
    settings = Settings()
    origins = settings.cors_allowed_origins_list

    # Vite dev + Tauri + same-host all in the default.
    assert "http://localhost:5173" in origins
    assert "tauri://localhost" in origins


def test_custom_origins_csv_is_split() -> None:
    settings = Settings(
        cors_allowed_origins="https://app.example.com,https://admin.example.com",
    )
    assert settings.cors_allowed_origins_list == [
        "https://app.example.com",
        "https://admin.example.com",
    ]


def test_origins_csv_trims_whitespace() -> None:
    settings = Settings(
        cors_allowed_origins=" https://a.test ,  https://b.test ",
    )
    assert settings.cors_allowed_origins_list == [
        "https://a.test",
        "https://b.test",
    ]


def test_origins_csv_drops_blank_entries() -> None:
    """A trailing comma in an env var is a common copy-paste mistake;
    don't treat the empty string as an origin (it would match nothing
    but pollute the allowlist count + logs)."""
    settings = Settings(
        cors_allowed_origins="https://a.test,,https://b.test,",
    )
    assert settings.cors_allowed_origins_list == [
        "https://a.test",
        "https://b.test",
    ]


def test_empty_origins_csv_yields_empty_list() -> None:
    """Setting `CORS_ALLOWED_ORIGINS=` explicitly disables CORS entirely
    (no middleware installed). Useful for an internal-only deploy."""
    settings = Settings(cors_allowed_origins="")
    assert settings.cors_allowed_origins_list == []
