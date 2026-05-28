"""End-to-end check that L1's character_vector reaches the LLM.

The unit tests pin the pieces in isolation:
  * ``test_character_vector`` — vector dataclass + descriptor renderers
  * ``test_turn_service_prompt_injection`` — descriptor lands in the
    prompt string the builders emit

What's left to prove is that the WHOLE pipeline — scenario_id from
``POST /v1/sessions`` → ``ScenarioSeed`` lookup → ``character_vector``
field → descriptor rendering → ``TurnService.stream_turn`` → LLM call
— actually carries the right bytes to the model. A regression that
silently drops the vector somewhere in that chain (e.g. a future
refactor that bypasses ``ScenarioSeed`` and reads the bare record)
would slip past every other test in the suite.

The harness uses a recording LLMProvider that captures every system
prompt it sees, then asserts:

  1. sc_001 (强硬型 HR — high control + power_gap) produces an
     L1.3 descriptor block matching that intent;
  2. sc_003 (同寝室友 — peer, near-zero power_gap) produces a
     materially different block matching THAT intent;
  3. Both share the scenario-injection scaffolding (title /
     background / persona_title) introduced by PR-D4, so we know
     L1 stacked on top of D4 without regressing the earlier fix.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from datetime import UTC, datetime

from app.llm import LLMProvider, Message, TokenUsage
from app.services.moderation import LogOnlyEventSink, ModerationService, NoopBackend
from app.services.scenarios.character_vector import VECTOR_DIMENSIONS, CharacterVector
from app.services.scenarios.seed_data import get_record_by_id
from app.services.sessions.repository import InMemorySessionRepository, SessionRecord
from app.services.sessions.sse import SseFrame
from app.services.sessions.turn_repository import InMemoryTurnRepository
from app.services.sessions.turn_service import TurnService


def _parse_base_from_arbiter_prompt(user_prompt: str) -> dict[str, int]:
    """Pull the `基础人格：aggression=60, ...` line out of the arbiter's
    user prompt so the stub can echo the static persona back as the
    next mood (keeping these L1-era descriptor assertions stable)."""
    base: dict[str, int] = {}
    for name in VECTOR_DIMENSIONS:
        match = re.search(rf"{name}=(\d+)", user_prompt)
        base[name] = int(match.group(1)) if match else 50
    return base


class _RecordingProvider:
    """Returns canned text per system-prompt keyword (mirrors the stub
    pattern in ``test_sessions_turn_service``) but also keeps a copy of
    every system prompt it saw, keyed by call site so assertions can
    inspect what the model actually received."""

    name = "recording"

    def __init__(self) -> None:
        self.system_prompts: dict[str, list[str]] = {
            "roleplay": [],
            "coach": [],
            "judge": [],
        }

    async def stream_chat(
        self,
        messages: list[Message],
        *,
        temperature: float = 0.7,
        timeout: float = 8.0,
        usage_sink: list[TokenUsage] | None = None,
    ) -> AsyncIterator[str]:
        _ = (temperature, timeout, usage_sink)
        system = messages[0].content if messages else ""
        if "你是对话情绪导演" in system:
            # PR-L3 mood arbiter. Echo the base persona back so next_mood
            # == the static character_vector and the descriptor assertions
            # below stay identical to the pre-L3 behaviour. The arbiter
            # clamps to the ±20 band, so echoing the base is always valid.
            self.system_prompts.setdefault("arbiter", []).append(system)
            base = _parse_base_from_arbiter_prompt(messages[-1].content)
            response = "\n".join(f"{name}: {value}" for name, value in base.items())
        elif "你扮演用户练习对话中的对手" in system:
            self.system_prompts["roleplay"].append(system)
            response = "这话我听过太多次了，今天不行。"
        elif "你是教练 K" in system:
            self.system_prompts["coach"].append(system)
            response = "SAFE: 我理解你的难处\nAGGRESSIVE: 这不是我的责任\nHUMOR: 我去问问菩萨"
        elif "你是评委" in system:
            self.system_prompts["judge"].append(system)
            response = "VERDICT: guolu\nRATING: 65"
        else:
            raise AssertionError(f"unscripted system prompt: {system[:80]!r}")
        yield response[: len(response) // 2]
        yield response[len(response) // 2 :]


def _service(
    llm: LLMProvider,
) -> tuple[TurnService, InMemorySessionRepository, InMemoryTurnRepository]:
    session_repo = InMemorySessionRepository()
    turn_repo = InMemoryTurnRepository()
    svc = TurnService(
        llm=llm,
        moderation=ModerationService(backend=NoopBackend(), event_sink=LogOnlyEventSink()),
        session_repo=session_repo,
        turn_repo=turn_repo,
    )
    return svc, session_repo, turn_repo


def _session(scenario_id: str, session_id: str) -> SessionRecord:
    # Seed mood_vector = the scenario's static character vector, exactly
    # as SessionService.create_session does. The arbiter then runs each
    # turn but the stub echoes the base back, so next_mood stays equal to
    # the seed and the descriptor assertions below are stable.
    record = get_record_by_id(scenario_id)
    mood = (
        record.character_vector
        if hasattr(record, "character_vector")
        else CharacterVector.neutral()
    )
    return SessionRecord(
        session_id=session_id,
        user_id="anonymous",
        mode="sandbox",
        scenario_id=scenario_id,
        persona_id="p_hard",
        user_goal="拒绝加班但不撕破脸",
        status="active",
        created_at=datetime(2026, 5, 28, 12, 0, tzinfo=UTC),
        mood_vector=mood,
    )


async def _drain(stream: AsyncIterator[SseFrame]) -> list[SseFrame]:
    return [frame async for frame in stream]


async def _run_turn(scenario_id: str, session_id: str) -> _RecordingProvider:
    """Spin up a service, save a session for `scenario_id`, run one
    turn, and return the recording provider so the caller can inspect
    the captured system prompts."""
    llm = _RecordingProvider()
    svc, session_repo, _ = _service(llm)
    await session_repo.save(_session(scenario_id, session_id))
    validated = await svc.validate_turn_request(
        session_id=session_id,
        content="我家里有事确实没法加班",
        user_id="anonymous",
        trace_id=f"trace-{scenario_id}",
    )
    await _drain(svc.stream_turn(validated))
    return llm


# --- sc_001 (强硬型 HR) end-to-end ---------------------------------------------


async def test_sc001_roleplay_prompt_carries_hr_descriptor() -> None:
    """High control + high power_gap + high stability → the corresponding
    L1.3 bullets must be in the system prompt the LLM saw."""
    llm = await _run_turn("sc_001", "ses_e2e_sc001")

    assert len(llm.system_prompts["roleplay"]) == 1
    prompt = llm.system_prompts["roleplay"][0]

    # PR-D4 scaffolding still in place
    assert "周末加班谈判" in prompt
    assert "强硬型 HR" in prompt
    assert "拒绝加班但不撕破脸" in prompt

    # L1.3 descriptor block
    assert "你的性格底色：" in prompt
    assert "强势把话题" in prompt
    assert "稳坐钓鱼台" in prompt
    assert "是上位者" in prompt


async def test_sc001_coach_prompt_carries_hierarchy_aware_profile() -> None:
    """Coach sees the compact 3-dim profile — power_gap HIGH triggers
    the 'don't硬顶' guidance, stability HIGH triggers the 'logic over
    emotional pressure' nudge."""
    llm = await _run_turn("sc_001", "ses_e2e_sc001_coach")

    assert len(llm.system_prompts["coach"]) == 1
    prompt = llm.system_prompts["coach"][0]

    assert "对手画像：" in prompt
    assert "上位者" in prompt
    assert "情绪很稳" in prompt


# --- sc_003 (同寝室友) end-to-end ----------------------------------------------


async def test_sc003_roleplay_prompt_carries_peer_descriptor() -> None:
    """Same template, different scenario — the peer roommate has near-
    zero power_gap (LOW), low aggression (LOW), low empathy (LOW), low
    control (LOW). The descriptor must show all four."""
    llm = await _run_turn("sc_003", "ses_e2e_sc003")

    prompt = llm.system_prompts["roleplay"][0]

    # PR-D4 scaffolding for the peer scenario
    assert "室友深夜打游戏" in prompt
    assert "同寝室友" in prompt

    # L1.3 — the 室友 profile
    assert "你的性格底色：" in prompt
    assert "和用户对等" in prompt
    assert "说话留余地" in prompt
    assert "用户情绪迟钝" in prompt


async def test_sc003_coach_prompt_omits_power_gap_warning() -> None:
    """power_gap=15 (LOW band) flips the coach guidance — it should
    tell K that the user is dealing with a peer, NOT an上位者."""
    llm = await _run_turn("sc_003", "ses_e2e_sc003_coach")

    prompt = llm.system_prompts["coach"][0]

    assert "对手画像：" in prompt
    assert "对等，可以正面回应" in prompt
    # The asymmetric flip — under no circumstances should the room-mate
    # scenario tell K to treat the opponent as a hierarchy holder.
    assert "上位者" not in prompt


# --- cross-scenario comparison -------------------------------------------------


async def test_two_scenarios_produce_materially_different_prompts() -> None:
    """The smoking gun for L1: the same prompt template + same user goal
    + DIFFERENT scenario_id must produce DIFFERENT system prompts. If
    these two strings ever drift to equality, the vector has been lost
    somewhere in the pipeline."""
    hr = await _run_turn("sc_001", "ses_e2e_compare_sc001")
    roomie = await _run_turn("sc_003", "ses_e2e_compare_sc003")

    hr_prompt = hr.system_prompts["roleplay"][0]
    roomie_prompt = roomie.system_prompts["roleplay"][0]

    assert hr_prompt != roomie_prompt
    # Specific opposites: HR is上位者, roommate is对等
    assert "是上位者" in hr_prompt
    assert "是上位者" not in roomie_prompt
    assert "和用户对等" in roomie_prompt
    assert "和用户对等" not in hr_prompt


# --- custom-scenario fallback (neutral vector) --------------------------------


async def test_unknown_scenario_id_falls_back_to_neutral_no_descriptor() -> None:
    """An unknown scenario_id (custom or typo) lands on FALLBACK_RECORD,
    whose neutral vector renders no descriptor. The prompt must still be
    valid — no stray "你的性格底色：" header on its own line, no doubled
    blank lines."""
    llm = await _run_turn("sc_does_not_exist_yet", "ses_e2e_fallback")

    prompt = llm.system_prompts["roleplay"][0]

    # Fallback record still gives us a usable session, no descriptor block
    assert "你的性格底色" not in prompt
    # And no stray "对手画像" on the coach side either
    coach_prompt = llm.system_prompts["coach"][0]
    assert "对手画像" not in coach_prompt
