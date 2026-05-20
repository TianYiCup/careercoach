"""`WeaknessService` behaviour — the communication-weakness profile."""

from __future__ import annotations

from datetime import UTC, datetime

from app.services.weakness import InMemoryWeaknessRepository, WeaknessRecord, WeaknessService

_NOW = datetime(2026, 5, 20, 8, 0, tzinfo=UTC)


def _service() -> tuple[WeaknessService, InMemoryWeaknessRepository]:
    repo = InMemoryWeaknessRepository()
    return WeaknessService(repo=repo), repo


async def test_apply_updates_folds_each_tag_into_the_profile() -> None:
    svc, _ = _service()
    await svc.apply_updates(
        user_id="u_1",
        tag_deltas={"过早让步": 1, "被绕话题": 2},
        now=_NOW,
    )
    rows = await svc.get_weaknesses("u_1")
    assert {r.tag: r.frequency for r in rows} == {"过早让步": 1, "被绕话题": 2}


async def test_apply_updates_accumulates_across_sessions() -> None:
    svc, _ = _service()
    await svc.apply_updates(user_id="u_1", tag_deltas={"过早让步": 1}, now=_NOW)
    await svc.apply_updates(user_id="u_1", tag_deltas={"过早让步": 1}, now=_NOW)
    rows = await svc.get_weaknesses("u_1")
    assert rows[0].frequency == 2


async def test_get_weaknesses_empty_for_user_never_scored() -> None:
    svc, _ = _service()
    assert await svc.get_weaknesses("u_nobody") == []


async def test_apply_updates_empty_dict_is_a_noop() -> None:
    """An empty session (0 turns → no weakness deltas) writes nothing."""
    svc, _ = _service()
    await svc.apply_updates(user_id="u_1", tag_deltas={}, now=_NOW)
    assert await svc.get_weaknesses("u_1") == []


async def test_apply_safe_swallows_repo_errors() -> None:
    """apply_safe must never raise — a weakness-store hiccup cannot be
    allowed to fail the session-end that calls it."""

    class _BoomRepo:
        async def list_for_user(self, user_id: str) -> list[WeaknessRecord]:  # pragma: no cover
            raise AssertionError("not reached")

        async def increment(
            self,
            *,
            user_id: str,
            tag: str,
            delta: int,
            now: datetime,
            fresh_id: str,
        ) -> None:
            raise RuntimeError("weakness store down")

    svc = WeaknessService(repo=_BoomRepo())
    # Must complete without raising.
    await svc.apply_safe(user_id="u_1", tag_deltas={"过早让步": 1}, now=_NOW)
