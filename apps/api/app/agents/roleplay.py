"""RolePlay node — generates the AI opponent's reply.

Sprint 0 stub: a deliberately minimal system prompt. Tone, persona,
and red-line guardrails are tightened in Sprint 1 once the moderation
service is live.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from app.agents._stream import stream_to_text
from app.agents.state import SessionState
from app.llm import LLMProvider, Message

ROLEPLAY_SYSTEM_PROMPT = (
    "你扮演用户练习对话中的对手，回应要自然、不超过 80 字，不要给建议，也不要破坏角色。"
)


def make_roleplay_node(
    provider: LLMProvider,
) -> Callable[[SessionState], Awaitable[SessionState]]:
    """Build a roleplay node bound to `provider`.

    Returning the closure (rather than exposing the provider as a node
    arg) keeps LangGraph's `add_node` signature happy — nodes are
    `Callable[[State], dict]`, not `Callable[[State, Deps], dict]`.
    """

    async def node(state: SessionState) -> SessionState:
        messages: list[Message] = [
            Message.system(ROLEPLAY_SYSTEM_PROMPT),
            *state.get("history", []),
            Message.user(state["user_turn"]),
        ]
        reply = await stream_to_text(provider.stream_chat(messages))
        return SessionState(opponent_reply=reply.strip())

    return node
