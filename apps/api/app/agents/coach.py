"""Coach K node — produces next-line guidance after seeing the opponent's reply.

Stub prompt only: K's full persona (嘴硬心软不爹味, 三档话术) gets
wired in Sprint 1 once the K voice prompt library lands.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from app.agents._stream import stream_to_text
from app.agents.state import SessionState
from app.llm import LLMProvider, Message

COACH_SYSTEM_PROMPT = (
    "你是教练 K。看完对手的回应，给用户一句 ≤ 40 字的下一句提示，不要替用户说话，不要爹味说教。"
)


def make_coach_node(
    provider: LLMProvider,
) -> Callable[[SessionState], Awaitable[SessionState]]:
    async def node(state: SessionState) -> SessionState:
        opponent_reply = state.get("opponent_reply", "")
        prompt = f"用户刚说：{state['user_turn']}\n对手回应：{opponent_reply}\n请给用户一句提示。"
        messages: list[Message] = [
            Message.system(COACH_SYSTEM_PROMPT),
            Message.user(prompt),
        ]
        hint = await stream_to_text(provider.stream_chat(messages))
        return SessionState(coach_hint=hint.strip())

    return node
