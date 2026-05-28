"""Behavioural tests for `SessionService` — the orchestrator that
connects sandbox session lifecycle to the sharecards data plane.

The single most important assertion in this file is the seam test:
`/end` must write the SessionCardData into the same SessionScoreRepo
that share-card service reads. If that link breaks, ending a session
no longer makes its card render — i.e. sharecards goes back to inert.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
from app.agents.state import TurnScore, Verdict
from app.llm import LLMAuthError, Message, TokenUsage
from app.schemas.sessions import CreateSessionRequest
from app.services.memory import InMemoryEpisodeRepository, MemoryService
from app.services.profile import InMemoryProfileRepository, ProfileService
from app.services.sessions import (
    InMemorySessionRepository,
    InMemoryTurnRepository,
    SessionAlreadyEndedError,
    SessionNotFoundError,
    SessionService,
)
from app.services.sessions.turn_repository import CoachHintTrio, TurnRecord
from app.services.sharecards.session_score import InMemorySessionScoreRepository

_DEFAULT_SUMMARY = (
    "AURA: 9\n"
    "LOGIC: 8\n"
    "EMOTION: 8\n"
    "PROFESSIONALISM: 9\n"
    "GOAL_ACHIEVE: 8\n"
    "HIGHLIGHTS: 把控住了节奏\n"
    "FAILURES: 末尾稍显急躁"
)


class _StubLLM:
    """Echoes a pre-canned summary so the aggregator's LLM branch is
    exercised without an upstream call. Default output is a clean
    shenfeng so empty-vs-present-turns tests can tell the branches
    apart by inspecting `score.result`."""

    name = "stub"

    def __init__(self, output: str = _DEFAULT_SUMMARY) -> None:
        self.output = output

    async def stream_chat(
        self,
        messages: list[Message],
        *,
        temperature: float = 0.7,
        timeout: float = 30.0,
        usage_sink: list[TokenUsage] | None = None,
    ) -> AsyncIterator[str]:
        del messages, temperature, timeout, usage_sink
        yield self.output


class _FailingLLM:
    """Models a fully-broken provider so the mechanical fallback path
    becomes the observed behaviour rather than dead code."""

    name = "failing"

    async def stream_chat(
        self,
        messages: list[Message],
        *,
        temperature: float = 0.7,
        timeout: float = 30.0,
        usage_sink: list[TokenUsage] | None = None,
    ) -> AsyncIterator[str]:
        del messages, temperature, timeout, usage_sink
        raise LLMAuthError("no creds in test", provider=self.name)
        yield ""  # pragma: no cover — unreachable


def _service(
    *,
    llm: _StubLLM | _FailingLLM | None = None,
) -> tuple[SessionService, InMemorySessionScoreRepository, InMemoryTurnRepository]:
    score_repo = InMemorySessionScoreRepository()
    turn_repo = InMemoryTurnRepository()
    svc = SessionService(
        repository=InMemorySessionRepository(),
        score_repo=score_repo,
        turn_repo=turn_repo,
        llm=llm or _StubLLM(),
        # Fresh profile + memory per service so L5/L6 state can't leak the
        # singletons' accumulated data across tests.
        profile_service=ProfileService(repo=InMemoryProfileRepository()),
        memory_service=MemoryService(repo=InMemoryEpisodeRepository()),
    )
    return svc, score_repo, turn_repo


def _request(scenario_id: str = "sc_001") -> CreateSessionRequest:
    return CreateSessionRequest(
        mode="sandbox",
        scenario_id=scenario_id,
        persona_id="p_hard",
        user_goal="保住周末，不得罪老板",
    )


async def _seed_one_turn(
    turn_repo: InMemoryTurnRepository,
    session_id: str,
    *,
    verdict: Verdict = Verdict.GUOLU,
    rating: int = 60,
) -> None:
    await turn_repo.append(
        TurnRecord(
            turn_id="t_seed0001",
            session_id=session_id,
            user_content="周末我有事",
            opponent_reply="那项目谁顶？",
            coach_hint=CoachHintTrio(safe="稳住", aggressive="刚回去", humor="搞笑一下"),
            turn_score=TurnScore(verdict=verdict, rating=rating),
            created_at=datetime.now(UTC),
        )
    )


async def test_create_session_returns_known_scenario_opening_line() -> None:
    svc, _, _ = _service()

    response = await svc.create_session(_request("sc_001"), user_id="u_test")

    assert response.session_id.startswith("ses_")
    assert len(response.session_id) > len("ses_")
    # sc_001 maps to the 周末加班谈判 seed.
    assert response.opening_line == "小林啊，这个周末项目得加个班，应该没问题吧？"


async def test_first_session_has_no_memory_payload() -> None:
    """L6: a brand-new (user, scenario) carries no recall."""
    svc, _, _ = _service()

    response = await svc.create_session(_request("sc_001"), user_id="u_fresh")

    assert response.memory is None


async def test_second_session_in_scenario_recalls_the_first() -> None:
    """L6 round-trip: create → seed a turn → end (records episode) →
    create again for the same user + scenario surfaces the memory."""
    svc, _, turn_repo = _service()

    first = await svc.create_session(_request("sc_001"), user_id="u_repeat")
    await _seed_one_turn(turn_repo, first.session_id, verdict=Verdict.FANCHE)
    end = await svc.end_session(first.session_id, user_id="u_repeat")

    second = await svc.create_session(_request("sc_001"), user_id="u_repeat")

    assert second.memory is not None
    assert second.memory.visit_count == 1
    # The recalled result is the *aggregated* session verdict (what the
    # scorecard showed), not any single turn's score.
    assert second.memory.last_result == end.score.result


async def test_memory_is_scoped_per_scenario() -> None:
    """Finishing sc_001 leaves sc_002 with no memory."""
    svc, _, turn_repo = _service()

    first = await svc.create_session(_request("sc_001"), user_id="u_scoped")
    await _seed_one_turn(turn_repo, first.session_id)
    await svc.end_session(first.session_id, user_id="u_scoped")

    other = await svc.create_session(_request("sc_002"), user_id="u_scoped")
    assert other.memory is None


async def test_empty_session_records_no_memory() -> None:
    """A session with no turns has no story — ending it must not create
    a recall the next session would surface."""
    svc, _, _ = _service()

    first = await svc.create_session(_request("sc_001"), user_id="u_empty")
    await svc.end_session(first.session_id, user_id="u_empty")

    second = await svc.create_session(_request("sc_001"), user_id="u_empty")
    assert second.memory is None


async def test_create_session_unknown_scenario_falls_back_to_generic_opening() -> None:
    """Sprint-1 catalog is sparse; unknown scenarios must still produce
    a session so we can demo against arbitrary ids while the catalog
    is growing."""
    svc, _, _ = _service()

    response = await svc.create_session(_request("sc_does_not_exist"), user_id="u_test")

    assert response.session_id.startswith("ses_")
    assert response.opening_line == "我们来聊聊吧。"


async def test_create_session_emits_unique_ids() -> None:
    svc, _, _ = _service()
    ids = {(await svc.create_session(_request(), user_id="u")).session_id for _ in range(8)}
    assert len(ids) == 8


async def test_end_session_with_turns_uses_llm_summary() -> None:
    svc, _, turn_repo = _service()
    created = await svc.create_session(_request(), user_id="u_test")
    await _seed_one_turn(turn_repo, created.session_id)

    end_response = await svc.end_session(created.session_id, user_id="u_test")

    s = end_response.score
    # The stub LLM returns a 9/8/8/9/8 shape → average 8.4 → shenfeng.
    assert s.aura == 9
    assert s.result == "shenfeng"
    assert "节奏" in s.highlights
    assert "急躁" in s.failures
    assert end_response.weakness_updates  # non-empty when turns exist


async def test_end_session_empty_session_returns_zeroes_and_no_weakness_updates() -> None:
    """Ending without any /turns must not crash and must not pretend
    the user produced any signal — every dim drops to 0 and the
    weakness list stays empty."""
    svc, _, _ = _service()
    created = await svc.create_session(_request(), user_id="u_test")

    end_response = await svc.end_session(created.session_id, user_id="u_test")

    s = end_response.score
    assert s.aura == 0
    assert s.logic == 0
    assert s.goal_achieve == 0
    assert s.result == "fanche"  # average 0 < 5 → fanche band
    assert s.highlights == "未开始练习"
    assert s.failures == "本次未发言"
    assert end_response.weakness_updates == []


async def test_end_session_with_failing_llm_falls_back_to_mechanical() -> None:
    """LLMAuthError mid-summary must NOT crash /end. The aggregator
    derives dims from the per-turn rating average so the response
    stays well-formed."""
    svc, _, turn_repo = _service(llm=_FailingLLM())
    created = await svc.create_session(_request(), user_id="u_test")
    await _seed_one_turn(
        turn_repo,
        created.session_id,
        verdict=Verdict.GUOLU,
        rating=60,
    )

    end_response = await svc.end_session(created.session_id, user_id="u_test")

    s = end_response.score
    # rating 60 / 10 = 6 across every dim → guolu (5 ≤ avg < 8).
    assert s.aura == 6
    assert s.logic == 6
    assert s.result == "guolu"
    # Mechanical narrative picks template copy keyed off the modal verdict.
    assert s.highlights == "态度稳得住，没翻车"


async def test_end_session_writes_card_data_to_shared_score_repo() -> None:
    """THE seam test — `/end` must populate the SessionScoreRepository
    that share-cards reads. Breaking this regresses sharecards to inert.
    """
    svc, score_repo, turn_repo = _service()
    created = await svc.create_session(_request("sc_001"), user_id="u_test")
    await _seed_one_turn(turn_repo, created.session_id)

    # Before /end, the score repo doesn't know about this session.
    assert await score_repo.get(created.session_id, user_id="u_test") is None

    await svc.end_session(created.session_id, user_id="u_test")

    card_data = await score_repo.get(created.session_id, user_id="u_test")
    assert card_data is not None
    # Scenario seed flows into the card payload — the renderer reads these.
    assert card_data.scenario_title == "周末加班谈判"
    assert card_data.persona_title == "强硬型 HR"
    # All five score dimensions made it through the adapter layer.
    assert 0 <= card_data.aura <= 10
    assert 0 <= card_data.goal_achieve <= 10
    assert card_data.result in {"shenfeng", "guolu", "fanche"}


async def test_end_session_marks_repository_status_ended() -> None:
    svc, _, _ = _service()
    created = await svc.create_session(_request(), user_id="u_test")

    await svc.end_session(created.session_id, user_id="u_test")

    record = await svc._repository.get(created.session_id)
    assert record is not None
    assert record.status == "ended"
    assert record.ended_at is not None


async def test_end_session_unknown_session_raises_not_found() -> None:
    svc, _, _ = _service()
    with pytest.raises(SessionNotFoundError):
        await svc.end_session("ses_never_created", user_id="u_test")


async def test_end_session_twice_raises_already_ended() -> None:
    svc, _, _ = _service()
    created = await svc.create_session(_request(), user_id="u_test")
    await svc.end_session(created.session_id, user_id="u_test")

    with pytest.raises(SessionAlreadyEndedError):
        await svc.end_session(created.session_id, user_id="u_test")
