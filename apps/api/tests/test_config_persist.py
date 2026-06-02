"""Config-time behaviour for the master `persist_backend` switch.

`PERSIST_BACKEND=postgres` fans out to every `*_repo_backend` field so a
demo / prod machine flips all persistence to Postgres in one move instead
of setting a dozen env vars. An explicit per-feature override still wins.

Hermetic (`_env_file=None`) so a local `apps/api/.env` can't sway the
assertions — these run the same locally and in CI.
"""

from __future__ import annotations

from app.config import Settings

_REPO_BACKEND_FIELDS = tuple(n for n in Settings.model_fields if n.endswith("_repo_backend"))


def test_repo_backends_default_to_memory() -> None:
    settings = Settings(_env_file=None)
    assert settings.persist_backend == "memory"
    assert all(getattr(settings, f) == "memory" for f in _REPO_BACKEND_FIELDS)


def test_persist_postgres_flips_every_repo_backend() -> None:
    settings = Settings(_env_file=None, persist_backend="postgres")
    # Sanity: there are several repos and they ALL flip — not a stale subset.
    assert len(_REPO_BACKEND_FIELDS) >= 10
    assert all(getattr(settings, f) == "postgres" for f in _REPO_BACKEND_FIELDS)


def test_individual_override_wins_over_master_switch() -> None:
    # An explicit memory pin on one feature survives `persist_backend=postgres`
    # (model_fields_set marks it as operator-chosen); the rest still flip.
    settings = Settings(
        _env_file=None,
        persist_backend="postgres",
        sessions_repo_backend="memory",
    )
    assert settings.sessions_repo_backend == "memory"
    others = [f for f in _REPO_BACKEND_FIELDS if f != "sessions_repo_backend"]
    assert all(getattr(settings, f) == "postgres" for f in others)


def test_persist_memory_is_a_noop() -> None:
    # The default master value must not touch an explicitly-postgres field.
    settings = Settings(
        _env_file=None,
        persist_backend="memory",
        review_repo_backend="postgres",
    )
    assert settings.review_repo_backend == "postgres"
    assert settings.sessions_repo_backend == "memory"
