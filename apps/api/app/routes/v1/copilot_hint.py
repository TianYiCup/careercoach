"""Copilot coach-hint generation + moderation (extracted from `copilot.py`).

This module owns the back half of one copilot utterance: input-side
moderation of the finalized transcript (`moderate_and_emit`) and the
background Coach K hint stream with its output-side moderation
(`stream_coach_hint`). The WS route in `copilot.py` orchestrates the
audio bridge and calls into here.

Split out so the route module stays under the file-size budget; behaviour
is unchanged — these are the same functions, relocated verbatim.
"""

from __future__ import annotations

import secrets
import time
from typing import Any

import structlog
from fastapi import WebSocket

from app.llm import LLMError, LLMProvider, Message, TokenUsage
from app.observability.langfuse import TurnTrace
from app.schemas.moderation import ModerationCheckRequest
from app.services.moderation import ModerationBackendError, ModerationService
from app.services.moderation.types import Decision

logger = structlog.get_logger(__name__)

COACH_SYSTEM_PROMPT = (
    "你是教练 K — 副驾模式。用户正在真实对话中（场景见上下文），对方刚说了一句话；"
    "请给用户一句 ≤ 40 字的下一句提示，不替用户说话，不爹味说教。"
)

# The copilot hint is a single ≤40-char line. The client waits for the
# full `hint_done` before it starts TTS, so the whole generation sits on
# the user-perceived latency path — a model that rambles to 100+ chars
# directly stretches the wait. Cap the completion so the tail is bounded
# (96 tokens ≫ 40 Han chars, enough headroom to never truncate a real
# hint mid-sentence) and pull the temperature down so the line stays
# direct instead of meandering.
COACH_HINT_MAX_TOKENS = 96
COACH_HINT_TEMPERATURE = 0.4


def elapsed_ms(start: float, end: float) -> float:
    """Elapsed milliseconds, rounded to one decimal — enough resolution
    for a latency log without the float noise of full precision."""
    return round((end - start) * 1000, 1)


async def moderate_and_emit(
    websocket: WebSocket,
    *,
    moderation_service: ModerationService,
    text: str,
    copilot_id: str,
    user_id: str,
) -> tuple[Decision | None, str | None]:
    """Score the finalized transcript and emit a `moderation` event.

    Returns `(decision, verdict_label)`:

      * Success:                `(decision, decision.verdict)`
      * Moderation backend down: `(None, "backend_failed")`   [A-37]
      * Any other exception:    `(None, None)`

    The caller uses `decision` to gate downstream hint generation
    (None → no hint spawns) and `verdict_label` to tag the Langfuse
    trace with `verdict_input:{label}`. Splitting the two lets us
    surface a backend outage as an observability signal without
    emitting a misleading "allow" decision the hint would then act
    on.

    Why a tuple instead of a sentinel Decision: `Decision.verdict`
    is typed `Literal["allow","warn","block","redirect"]`. Adding a
    `backend_failed` member would propagate into every downstream
    that pattern-matches on the verdict (DB persistence, scorecard
    aggregation, etc.) — far broader blast radius than a localized
    label string used only for the Langfuse tag.

    Strictness
    ----------
    `is_minor=False` is hardcoded because copilot is adult-only — the
    POST handler's `require_adult` guarantees no minor JWT can mint a
    pending session in the first place. If a future PR widens copilot
    to teens (it shouldn't — R-15) this needs to plumb the flag from
    the JWT through the session record.
    """
    trace_id = "cop_mod_" + secrets.token_hex(8)
    request = ModerationCheckRequest(content=text, context="user_input")
    try:
        response = await moderation_service.check(
            request,
            user_id=user_id,
            is_minor=False,
            trace_id=trace_id,
        )
    except ModerationBackendError as exc:
        # A-37: distinguished from the generic catch-all below so the
        # Langfuse trace gets a `verdict_input:backend_failed` tag
        # instead of being silently untagged. We deliberately do NOT
        # emit a `moderation` WS frame here — clients today don't
        # know about a `backend_failed` verdict and a synthetic
        # `allow` would be misleading. Skipping the frame is the
        # safe default; the trace tag is the ops-visible signal.
        logger.warning(
            "copilot_input_moderation_unavailable",
            copilot_id=copilot_id,
            trace_id=trace_id,
            backend=exc.backend,
            error=str(exc),
        )
        return None, "backend_failed"
    except Exception:
        logger.exception(
            "copilot_moderation_failed",
            copilot_id=copilot_id,
            trace_id=trace_id,
        )
        return None, None

    payload: dict[str, Any] = {
        "type": "moderation",
        "verdict": response.verdict,
        "categories": response.categories,
        "score": response.score,
        "redirect_resource": (
            response.redirect_resource.model_dump()
            if response.redirect_resource is not None
            else None
        ),
    }
    await websocket.send_json(payload)

    return (
        Decision(
            verdict=response.verdict,
            score=response.score,
            categories=tuple(response.categories),
            redirect_resource=response.redirect_resource,
        ),
        response.verdict,
    )


async def stream_coach_hint(
    websocket: WebSocket,
    *,
    llm_router: LLMProvider,
    moderation_service: ModerationService,
    scenario_hint: str,
    user_input: str,
    copilot_id: str,
    user_id: str,
    trace: TurnTrace,
) -> None:
    """Generate a Coach K hint for one utterance and stream it back.

    Runs as a background task — concurrent with the next utterance's
    audio loop. Each delta arrives as `{"type":"hint_delta","text":...}`;
    the joined transcript closes the run as `{"type":"hint_done","text":...}`.

    Empty hints emit nothing. The router yielding zero non-empty
    chunks (rare, but possible with a misbehaving prompt or upstream)
    converges on "no events" rather than an empty `hint_done` so the
    client's UI doesn't flash a blank tip card.

    Records a `coach_hint` generation on the parent utterance trace
    iff the call produced any text. LLM-error paths skip the
    generation — the failure is captured by the structured log and
    the `hint_error` event the user sees; an extra empty Langfuse
    span would be noise.

    A-32: after the hint stream completes, run output moderation on
    the joined text. On `block` / `redirect` we suppress `hint_done`
    and emit `hint_error` instead — same pattern as A-29 in sandbox.
    The deltas already streamed to the client (we can't unsend them)
    but the authoritative `hint_done` is replaced by the
    "hint unavailable" error envelope, and the persisted Langfuse
    trace carries a `verdict_output:{...}` tag so analysts can
    triage hints that produced red-line content.

    Failure modes
    -------------
      * `LLMError` (auth / timeout / upstream)  → log + emit one
        `hint_error` event with a stable opaque message; the WS stays
        open so the user can keep speaking.
      * Output moderation `block` / `redirect`  → emit `hint_error`
        instead of `hint_done`; record the generation so Langfuse
        shows what tripped the gate.
      * WS already closed when we try to send → return silently. The
        outer handler will await this task in `finally` and discard
        the return value.
    """
    messages: list[Message] = [
        Message.system(COACH_SYSTEM_PROMPT),
        Message.user(_build_coach_user_prompt(scenario_hint, user_input)),
    ]
    parts: list[str] = []
    usage: list[TokenUsage] = []
    # P0 latency instrumentation. `first_token_at` isolates the LLM's
    # time-to-first-token (the dominant cost for a short hint) from the
    # full-generation time, so the waterfall log can tell "model was slow
    # to start" apart from "model rambled". Wall-clock via perf_counter
    # — monotonic, immune to clock adjustments.
    started_at = time.perf_counter()
    first_token_at: float | None = None
    try:
        async for chunk in llm_router.stream_chat(
            messages,
            usage_sink=usage,
            temperature=COACH_HINT_TEMPERATURE,
            max_tokens=COACH_HINT_MAX_TOKENS,
        ):
            if not chunk:
                continue
            if first_token_at is None:
                first_token_at = time.perf_counter()
            if not await _send_or_drop(
                websocket,
                {"type": "hint_delta", "text": chunk},
                copilot_id=copilot_id,
            ):
                return
            parts.append(chunk)
    except LLMError:
        logger.exception("copilot_hint_llm_failed", copilot_id=copilot_id)
        await _send_or_drop(
            websocket,
            {"type": "hint_error", "message": "hint unavailable"},
            copilot_id=copilot_id,
        )
        return

    llm_done_at = time.perf_counter()
    full = "".join(parts)
    if not full:
        return
    trace.record_generation(
        name="coach_hint",
        model=llm_router.name,
        input={"scenario_hint": scenario_hint, "user_input": user_input},
        output={"text": full},
        usage=usage[0] if usage else None,
    )

    # A-32: output-side moderation on the hint text. block/redirect
    # suppress hint_done and emit hint_error instead (same envelope
    # the LLM-error path uses, so frontend handling stays unified).
    mod_started_at = time.perf_counter()
    output_passed, output_verdict = await _hint_output_passes_moderation(
        full,
        moderation_service=moderation_service,
        copilot_id=copilot_id,
        user_id=user_id,
    )
    mod_done_at = time.perf_counter()
    if output_verdict is not None:
        trace.add_tags([f"verdict_output:{output_verdict}"])
    if not output_passed:
        logger.warning(
            "copilot_hint_output_blocked",
            copilot_id=copilot_id,
            verdict=output_verdict,
        )
        await _send_or_drop(
            websocket,
            {"type": "hint_error", "message": "hint unavailable"},
            copilot_id=copilot_id,
        )
        return

    # P0 waterfall: the hint half of the backend pipeline. `elapsed_ms` so
    # the log greps cleanly into a latency dashboard without unit parsing.
    _log_hint_latency(
        copilot_id=copilot_id,
        started_at=started_at,
        first_token_at=first_token_at,
        llm_done_at=llm_done_at,
        mod_started_at=mod_started_at,
        mod_done_at=mod_done_at,
        char_count=len(full),
    )
    await _send_or_drop(
        websocket,
        {"type": "hint_done", "text": full},
        copilot_id=copilot_id,
    )


def _log_hint_latency(
    *,
    copilot_id: str,
    started_at: float,
    first_token_at: float | None,
    llm_done_at: float,
    mod_started_at: float,
    mod_done_at: float,
    char_count: int,
) -> None:
    """Emit the coach-hint latency breakdown as one structured log line.

    Split so the call site stays readable and so the field set lives in
    one place. `llm_first_token_ms` is None when the stream yielded no
    text before completing — distinguishes "slow first token" from
    "produced nothing".
    """
    logger.info(
        "copilot_hint_latency",
        copilot_id=copilot_id,
        llm_first_token_ms=(
            elapsed_ms(started_at, first_token_at) if first_token_at is not None else None
        ),
        llm_total_ms=elapsed_ms(started_at, llm_done_at),
        output_moderation_ms=elapsed_ms(mod_started_at, mod_done_at),
        char_count=char_count,
    )


async def _hint_output_passes_moderation(
    hint_text: str,
    *,
    moderation_service: ModerationService,
    copilot_id: str,
    user_id: str,
) -> tuple[bool, str | None]:
    """Re-check the coach hint text against the red-line list.

    Returns `(passes, verdict)`:
      * `passes`: True when the hint is safe to surface as `hint_done`;
                  False on `block` / `redirect`.
      * `verdict`: literal moderation verdict for trace tagging.
                  One of `"allow"|"warn"|"redirect"|"block"` (real
                  backend decision) or `"backend_failed"` (A-34
                  sentinel — backend raised, we still ship
                  `hint_done` but the trace surfaces the outage via
                  `verdict_output:backend_failed`). Never `None`
                  for hint mod: hint_text is always non-empty when
                  we get here (the caller short-circuits empties).

    The user's input was already moderated upstream in
    `moderate_and_emit`. This is the symmetric check on the LLM's
    output, mirroring A-29 sandbox. Copilot is adult-only by R-15
    so `is_minor=False` is hardcoded — no JWT thread needed.
    """
    try:
        decision = await moderation_service.check(
            ModerationCheckRequest(
                content=hint_text,
                context="ai_output",
                session_id=copilot_id,
            ),
            user_id=user_id,
            is_minor=False,
            trace_id=copilot_id,
        )
    except ModerationBackendError as exc:
        logger.warning(
            "copilot_hint_output_moderation_unavailable",
            copilot_id=copilot_id,
            error=str(exc),
        )
        # A-34: return the `"backend_failed"` sentinel verdict so the
        # caller's existing `verdict_output:{verdict}` tag adder
        # surfaces hint-mod outages on Langfuse. `passes=True` keeps
        # the treat-as-allow UX — hint_done still ships — but the
        # trace clearly shows mod didn't actually run. Same sentinel
        # used by sandbox + review for cross-surface analyst queries.
        return True, "backend_failed"

    if decision.verdict in ("block", "redirect"):
        return False, decision.verdict
    return True, decision.verdict


def _build_coach_user_prompt(scenario_hint: str, user_input: str) -> str:
    return (
        f"用户当前场景：{scenario_hint}\n对方刚说：「{user_input}」\n请给用户一句下一句话的提示。"
    )


async def _send_or_drop(
    websocket: WebSocket,
    payload: dict[str, Any],
    *,
    copilot_id: str,
) -> bool:
    """Send a hint event, returning False if the WS is already closed.

    Background hint tasks may outlive the active connection (the user
    disconnects mid-hint). We don't want a routine WS-closed exception
    to bubble up — log it once and return False so the caller stops
    sending more.
    """
    try:
        await websocket.send_json(payload)
        return True
    except Exception:
        logger.info(
            "copilot_hint_send_dropped",
            copilot_id=copilot_id,
            payload_type=payload.get("type"),
        )
        return False
