"""Unit tests for the MoodArbiter (Character Engine L3).

Covers the three things that can go wrong in production:
  * the LLM returns a clean labelled block → parse + clamp;
  * the LLM returns garbage / partial → fall back to prev_mood;
  * the LLM raises (timeout / network) → fall back to prev_mood.

Plus the drift-band clamp that keeps a persona from flipping on one
turn, which is the invariant the whole L3 design rests on.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from app.llm import Message, TokenUsage
from app.services.scenarios.character_vector import CharacterVector
from app.services.sessions.mood_arbiter import MOOD_DRIFT_BAND, MoodArbiter

# 强硬型 HR base, mirrors persona_vectors.sc_001.
BASE = CharacterVector(
    aggression=60,
    empathy=30,
    control=75,
    honesty=50,
    stability=80,
    power_gap=70,
)


class _ScriptedLLM:
    """Yields a fixed response, ignoring the prompt."""

    name = "scripted"

    def __init__(self, response: str) -> None:
        self._response = response

    async def stream_chat(
        self,
        messages: list[Message],
        *,
        temperature: float = 0.7,
        timeout: float = 8.0,
        usage_sink: list[TokenUsage] | None = None,
    ) -> AsyncIterator[str]:
        _ = (messages, temperature, timeout, usage_sink)
        yield self._response


class _RaisingLLM:
    name = "raising"

    async def stream_chat(
        self,
        messages: list[Message],
        *,
        temperature: float = 0.7,
        timeout: float = 8.0,
        usage_sink: list[TokenUsage] | None = None,
    ) -> AsyncIterator[str]:
        _ = (messages, temperature, timeout, usage_sink)
        raise TimeoutError("simulated arbiter timeout")
        yield ""  # pragma: no cover — unreachable, makes this an async generator


async def _arbitrate(llm: object, *, prev_mood: CharacterVector | None = None) -> CharacterVector:
    arbiter = MoodArbiter(llm)  # type: ignore[arg-type]
    return await arbiter.next_mood(
        character_vector=BASE,
        prev_mood=prev_mood if prev_mood is not None else BASE,
        user_content="我家里确实有事，这周末真的没办法",
        opponent_last_reply="这个项目很重要，你再想想",
        trace_id="t1",
        session_id="ses_test",
    )


async def test_parses_clean_labelled_block() -> None:
    response = "aggression: 55\nempathy: 38\ncontrol: 70\nhonesty: 50\nstability: 78\npower_gap: 70"

    mood = await _arbitrate(_ScriptedLLM(response))

    assert mood.aggression == 55
    assert mood.empathy == 38
    assert mood.control == 70


async def test_clamps_to_drift_band_above() -> None:
    """LLM tries to push aggression to 100 (a +40 jump). The clamp pulls
    it back to base + MOOD_DRIFT_BAND so the HR can't turn into a
    screaming maniac on one turn."""
    response = (
        "aggression: 100\nempathy: 30\ncontrol: 75\nhonesty: 50\nstability: 80\npower_gap: 70"
    )

    mood = await _arbitrate(_ScriptedLLM(response))

    assert mood.aggression == BASE.aggression + MOOD_DRIFT_BAND  # 60 + 20 = 80


async def test_clamps_to_drift_band_below() -> None:
    """And the floor: a 0 prediction on stability clamps to base - 20."""
    response = "aggression: 60\nempathy: 30\ncontrol: 75\nhonesty: 50\nstability: 0\npower_gap: 70"

    mood = await _arbitrate(_ScriptedLLM(response))

    assert mood.stability == BASE.stability - MOOD_DRIFT_BAND  # 80 - 20 = 60


async def test_clamp_respects_zero_hundred_bounds() -> None:
    """When base is near an edge, the drift band can't escape 0-100. A
    base power_gap of 10 with a -20 drift floors at 0, not -10."""
    edge_base = CharacterVector(
        aggression=50,
        empathy=50,
        control=50,
        honesty=50,
        stability=50,
        power_gap=10,
    )
    response = "aggression: 50\nempathy: 50\ncontrol: 50\nhonesty: 50\nstability: 50\npower_gap: 0"
    arbiter = MoodArbiter(_ScriptedLLM(response))

    mood = await arbiter.next_mood(
        character_vector=edge_base,
        prev_mood=edge_base,
        user_content="x",
        opponent_last_reply=None,
        trace_id="t",
        session_id="s",
    )

    assert mood.power_gap == 0


async def test_falls_back_to_prev_mood_on_partial_output() -> None:
    """Missing the `stability` line → unparseable → prev_mood returned
    unchanged. The turn must never block on the arbiter."""
    response = "aggression: 55\nempathy: 38\ncontrol: 70\nhonesty: 50\npower_gap: 70"
    prev = CharacterVector(
        aggression=58,
        empathy=32,
        control=72,
        honesty=50,
        stability=79,
        power_gap=70,
    )

    mood = await _arbitrate(_ScriptedLLM(response), prev_mood=prev)

    assert mood == prev


async def test_falls_back_to_prev_mood_on_garbage() -> None:
    mood = await _arbitrate(_ScriptedLLM("对不起我不太明白你的意思"))

    assert mood == BASE  # prev_mood defaulted to BASE in the helper


async def test_falls_back_to_prev_mood_on_llm_exception() -> None:
    prev = CharacterVector(
        aggression=58,
        empathy=32,
        control=72,
        honesty=50,
        stability=79,
        power_gap=70,
    )

    mood = await _arbitrate(_RaisingLLM(), prev_mood=prev)

    assert mood == prev


async def test_handles_fullwidth_colon() -> None:
    """The system prompt shows half-width colons but a model might echo
    the Chinese full-width `：`. The regex accepts both."""
    response = (
        "aggression： 55\nempathy： 38\ncontrol： 70\nhonesty： 50\nstability： 78\npower_gap： 70"
    )

    mood = await _arbitrate(_ScriptedLLM(response))

    assert mood.aggression == 55
    assert mood.empathy == 38


# --- PR-OPT2: the merged stage + mood call (middle-window path) ---------


async def _arbitrate_with_stage(
    llm: object, *, prev_mood: CharacterVector | None = None
) -> tuple[str, CharacterVector]:
    arbiter = MoodArbiter(llm)  # type: ignore[arg-type]
    return await arbiter.next_mood_with_stage(
        character_vector=BASE,
        prev_mood=prev_mood if prev_mood is not None else BASE,
        user_content="我家里确实有事，这周末真的没办法",
        opponent_last_reply="这个项目很重要，你再想想",
        trace_id="t1",
        session_id="ses_test",
    )


async def test_with_stage_parses_stage_and_mood() -> None:
    """One call yields both the dramatic stage and a clamped mood."""
    response = (
        "stage: turning\n"
        "aggression: 55\nempathy: 38\ncontrol: 70\nhonesty: 50\nstability: 78\npower_gap: 70"
    )

    stage, mood = await _arbitrate_with_stage(_ScriptedLLM(response))

    assert stage == "turning"
    assert mood.aggression == 55
    assert mood.empathy == 38


async def test_with_stage_clamps_mood_to_drift_band() -> None:
    """The merged path clamps the same way the mood-only path does."""
    response = (
        "stage: conflict\n"
        "aggression: 100\nempathy: 30\ncontrol: 75\nhonesty: 50\nstability: 80\npower_gap: 70"
    )

    stage, mood = await _arbitrate_with_stage(_ScriptedLLM(response))

    assert stage == "conflict"
    assert mood.aggression == BASE.aggression + MOOD_DRIFT_BAND  # clamped to 80


async def test_with_stage_unparseable_stage_falls_back_to_conflict() -> None:
    """Mood parses but the stage line is missing → conflict (keep
    pressing), mood still applied."""
    response = "aggression: 55\nempathy: 38\ncontrol: 70\nhonesty: 50\nstability: 78\npower_gap: 70"

    stage, mood = await _arbitrate_with_stage(_ScriptedLLM(response))

    assert stage == "conflict"
    assert mood.aggression == 55


async def test_with_stage_partial_mood_keeps_stage_falls_back_mood() -> None:
    """Stage parses but the mood block is incomplete → keep the stage,
    fall back to prev_mood."""
    prev = CharacterVector(
        aggression=58, empathy=32, control=72, honesty=50, stability=79, power_gap=70
    )
    response = "stage: closing\naggression: 55\nempathy: 38\ncontrol: 70\npower_gap: 70"

    stage, mood = await _arbitrate_with_stage(_ScriptedLLM(response), prev_mood=prev)

    assert stage == "closing"
    assert mood == prev


async def test_with_stage_llm_exception_returns_conflict_and_prev_mood() -> None:
    prev = CharacterVector(
        aggression=58, empathy=32, control=72, honesty=50, stability=79, power_gap=70
    )

    stage, mood = await _arbitrate_with_stage(_RaisingLLM(), prev_mood=prev)

    assert stage == "conflict"
    assert mood == prev
