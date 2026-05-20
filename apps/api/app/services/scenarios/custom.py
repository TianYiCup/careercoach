"""Custom scenario creation — PRD §7.3 / US-A1.

`POST /v1/scenarios/custom` turns a free-text description into a
practisable scenario:

  1. a 10/day per-user quota (PRD §7.12);
  2. input moderation of the description (§3.0.5 D — 自定义场景必过审);
  3. LLM generation of the scenario (title / persona / opening line);
  4. output moderation of the generated scenario (§3.0.5 D — the
     opponent persona is adversarial by design, so a benign
     description can still yield a red-line script);
  5. in-process registration so a follow-up `POST /v1/sessions` can
     practise the new scenario_id immediately.

The generation LLM call runs under a Langfuse `custom_scenario` trace
(CLAUDE.md §5). Durable storage is a later epic — v1 runs single-worker
with memory backends throughout.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import AsyncIterator
from datetime import date, datetime
from zoneinfo import ZoneInfo

import structlog
from langfuse import Langfuse

from app.llm import LLMProvider, Message, TokenUsage
from app.observability.langfuse import begin_scenario_trace
from app.schemas.moderation import ModerationCheckRequest, ModerationCheckResponse, RedirectResource
from app.services.moderation import ModerationBackendError
from app.services.moderation.service import ModerationService
from app.services.scenarios.seed_data import ScenarioRecord, register_custom_scenario

logger = structlog.get_logger(__name__)

# PRD §7.12 — 10 custom scenarios per user per day. The quota guards
# the LLM generation cost; a blocked description never reaches the
# generator, so it does not consume quota.
_DAILY_QUOTA = 10

# A streak/vibe-style Asia/Shanghai day boundary (CLAUDE.md §6) — the
# quota resets on the user's local midnight, not UTC's.
_SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")

_TITLE_MAX_CHARS = 20

# Scenario-generation prompt. Strict labelled output so the parser
# below can pull four fields without the LLM wandering into prose.
_GEN_PROMPT = (
    "你是对练场景设计师。根据用户的描述，设计一个 1 对 1 对话练习场景。"
    "严格按以下四行格式输出，每行一项，不要解释、不要任何额外文字：\n"
    "TITLE: <场景标题，≤20字>\n"
    "PERSONA: <对手身份，≤12字>\n"
    "OPENING: <对手开口的第一句话，≤40字>\n"
    "BACKGROUND: <场景背景，≤80字>"
)

_TITLE_RE = re.compile(r"TITLE\s*[:：]\s*(.+)", re.IGNORECASE)
_PERSONA_RE = re.compile(r"PERSONA\s*[:：]\s*(.+)", re.IGNORECASE)
_OPENING_RE = re.compile(r"OPENING\s*[:：]\s*(.+)", re.IGNORECASE)
_BACKGROUND_RE = re.compile(r"BACKGROUND\s*[:：]\s*(.+)", re.IGNORECASE)


class CustomScenarioRateLimitedError(RuntimeError):
    """Route maps to 429 — caller hit the daily custom-scenario quota."""

    def __init__(self, *, limit: int) -> None:
        super().__init__(f"daily custom-scenario quota of {limit} reached")
        self.limit = limit


class CustomScenarioBlockedError(RuntimeError):
    """Route maps to 400 — moderation rejected the scenario (§3.0.5 D:
    自定义场景必过审). Carries the crisis resource only when the *input*
    description tripped a `redirect` verdict (a user describing
    self-harm) — a red-line *generated* script is LLM drift, not a
    user in distress."""

    def __init__(
        self,
        *,
        categories: tuple[str, ...],
        redirect_resource: RedirectResource | None = None,
    ) -> None:
        super().__init__(f"custom scenario blocked: {categories}")
        self.categories = categories
        self.redirect_resource = redirect_resource


class CustomScenarioService:
    """Validates, moderates, generates, and registers user-created scenarios."""

    def __init__(
        self,
        *,
        moderation: ModerationService,
        llm: LLMProvider,
        langfuse_client: Langfuse | None = None,
    ) -> None:
        self._moderation = moderation
        self._llm = llm
        # `None` is supported — `begin_scenario_trace` returns a no-op
        # trace so dev runs without Langfuse keys behave identically.
        self._langfuse_client = langfuse_client
        # (user_id, Shanghai date) -> count. In-memory v1 — a restart
        # resets quotas, acceptable for a 10/day abuse guard.
        self._quota: dict[tuple[str, date], int] = {}

    async def create_custom_scenario(
        self,
        *,
        description: str,
        user_id: str,
        is_minor: bool,
        trace_id: str,
        today: date | None = None,
    ) -> ScenarioRecord:
        """Moderate, generate, moderate again, register, and return the
        scenario. Raises `CustomScenarioRateLimitedError` (quota) or
        `CustomScenarioBlockedError` (moderation). `today` is injectable
        so tests pin the quota day."""
        day = today or datetime.now(_SHANGHAI_TZ).date()
        self._check_quota(user_id=user_id, day=day)

        # 1. Input moderation — the user's description must pass review.
        input_decision = await self._moderate(
            description, user_id=user_id, is_minor=is_minor, trace_id=trace_id
        )
        self._raise_if_unsafe(input_decision, user_id=user_id, trace_id=trace_id, stage="input")

        # 2. LLM generation.
        record = await self._generate(description, user_id=user_id, trace_id=trace_id)

        # 3. Output moderation — the generated scenario must pass review
        #    too. The opponent persona + opening line are adversarial by
        #    design, so the model can drift into red-line territory even
        #    from a benign description.
        output_decision = await self._moderate(
            _scenario_text(record), user_id=user_id, is_minor=is_minor, trace_id=trace_id
        )
        self._raise_if_unsafe(output_decision, user_id=user_id, trace_id=trace_id, stage="output")

        register_custom_scenario(record)
        self._quota[(user_id, day)] = self._quota.get((user_id, day), 0) + 1
        logger.info(
            "custom_scenario_created",
            user_id=user_id,
            trace_id=trace_id,
            scenario_id=record.id,
        )
        return record

    def _check_quota(self, *, user_id: str, day: date) -> None:
        if self._quota.get((user_id, day), 0) >= _DAILY_QUOTA:
            raise CustomScenarioRateLimitedError(limit=_DAILY_QUOTA)

    def _raise_if_unsafe(
        self,
        decision: ModerationCheckResponse | None,
        *,
        user_id: str,
        trace_id: str,
        stage: str,
    ) -> None:
        """Reject a `block` / `redirect` verdict. `decision is None`
        (backend outage) is treat-as-allow — see `_moderate`."""
        if decision is None or decision.verdict not in ("block", "redirect"):
            return
        logger.info(
            "custom_scenario_blocked",
            user_id=user_id,
            trace_id=trace_id,
            stage=stage,
            verdict=decision.verdict,
            categories=list(decision.categories),
        )
        raise CustomScenarioBlockedError(
            categories=tuple(decision.categories),
            redirect_resource=decision.redirect_resource if stage == "input" else None,
        )

    async def _moderate(
        self,
        content: str,
        *,
        user_id: str,
        is_minor: bool,
        trace_id: str,
    ) -> ModerationCheckResponse | None:
        """Check `content` against the red lines. Returns `None` on a
        backend outage — fail-open mirrors the turn paths (A-34/A-37);
        an Aliyun blip must not deny scenario creation outright."""
        try:
            return await self._moderation.check(
                ModerationCheckRequest(content=content, context="scenario_custom"),
                user_id=user_id,
                is_minor=is_minor,
                trace_id=trace_id,
            )
        except ModerationBackendError as exc:
            logger.warning(
                "custom_scenario_moderation_unavailable",
                user_id=user_id,
                trace_id=trace_id,
                error=str(exc),
            )
            return None

    async def _generate(
        self,
        description: str,
        *,
        user_id: str,
        trace_id: str,
    ) -> ScenarioRecord:
        """LLM-generate a scenario from `description`, under a Langfuse
        `custom_scenario` trace (CLAUDE.md §5). Falls back to a templated
        placeholder if the model ignores the labelled output format — the
        user still gets a practisable scenario."""
        trace = begin_scenario_trace(
            self._langfuse_client,
            input={"description": description},
            metadata={"trace_id": trace_id},
            trace_id=trace_id,
            user_id=user_id,
        )
        try:
            messages = [Message.system(_GEN_PROMPT), Message.user(description)]
            usage: list[TokenUsage] = []
            raw = await _collect(self._llm.stream_chat(messages, usage_sink=usage))
            trace.record_generation(
                name="custom_scenario.generate",
                model=self._llm.name,
                input=[m.model_dump() for m in messages],
                output=raw,
                usage=usage[0] if usage else None,
            )
            record = _parse_generated(raw)
            if record is None:
                logger.warning(
                    "custom_scenario_generation_unparseable",
                    trace_id=trace_id,
                    raw=raw[:200],
                )
                record = _build_placeholder(description)
            trace.finish(output={"scenario_id": record.id, "title": record.title})
            return record
        except Exception as exc:
            trace.fail(exc)
            raise


def _scenario_text(record: ScenarioRecord) -> str:
    """The generated scenario's human-readable text, for output
    moderation — title + persona + opening line + background."""
    return "\n".join([record.title, record.persona_title, record.opening_line, record.background])


def _parse_generated(raw: str) -> ScenarioRecord | None:
    """Best-effort parse of the four labelled lines. Returns `None` when
    any field is missing so the caller can fall back to the placeholder."""
    title = _TITLE_RE.search(raw)
    persona = _PERSONA_RE.search(raw)
    opening = _OPENING_RE.search(raw)
    background = _BACKGROUND_RE.search(raw)
    if not (title and persona and opening and background):
        return None
    return ScenarioRecord(
        id=f"sc_custom_{uuid.uuid4().hex[:12]}",
        title=title.group(1).strip()[:_TITLE_MAX_CHARS],
        category="life",  # custom scenarios are uncategorised — default
        difficulty=3,  # mid — the picker only surfaces difficulty for catalog rows
        tags=(),
        background=background.group(1).strip(),
        real_user_certified=False,
        persona_title=persona.group(1).strip(),
        opening_line=opening.group(1).strip(),
    )


def _build_placeholder(description: str) -> ScenarioRecord:
    """Fallback generator — a templated scenario carrying the raw
    description as its background. Used when the LLM ignores the
    labelled output format so the user still gets something practisable."""
    return ScenarioRecord(
        id=f"sc_custom_{uuid.uuid4().hex[:12]}",
        title=_derive_title(description),
        category="life",
        difficulty=3,
        tags=(),
        background=description,
        real_user_certified=False,
        persona_title="对手",
        opening_line="我们开始吧，你先说。",
    )


def _derive_title(description: str) -> str:
    """First line of the description, capped — a stand-in title for the
    placeholder fallback."""
    first_line = description.strip().splitlines()[0].strip()
    return first_line[:_TITLE_MAX_CHARS]


async def _collect(stream: AsyncIterator[str]) -> str:
    """Drain an async token stream into one string."""
    parts: list[str] = []
    async for chunk in stream:
        parts.append(chunk)
    return "".join(parts)


__all__ = [
    "CustomScenarioBlockedError",
    "CustomScenarioRateLimitedError",
    "CustomScenarioService",
]
