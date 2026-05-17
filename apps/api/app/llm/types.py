"""Provider-agnostic message types passed to `LLMProvider.stream_chat`.

Kept deliberately small: only what every adapter needs. Anything
provider-specific (function calls, image parts, etc.) belongs in the
adapter module, not here.
"""

from dataclasses import dataclass
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class Role(StrEnum):
    """OpenAI/DeepSeek-compatible message roles.

    Kept as a StrEnum so adapters can serialize to the wire format
    (`{"role": "user", ...}`) with `role.value` or implicit str cast.
    """

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"


class Message(BaseModel):
    """Single chat message in the conversation history."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    role: Role
    content: str = Field(min_length=1)

    @classmethod
    def system(cls, content: str) -> "Message":
        return cls(role=Role.SYSTEM, content=content)

    @classmethod
    def user(cls, content: str) -> "Message":
        return cls(role=Role.USER, content=content)

    @classmethod
    def assistant(cls, content: str) -> "Message":
        return cls(role=Role.ASSISTANT, content=content)


@dataclass(frozen=True)
class TokenUsage:
    """Per-call token accounting.

    Matches the OpenAI/DeepSeek/Qwen wire shape so the adapter layer
    can lift it straight off the final SSE chunk. Surfaced to callers
    via the optional `usage_sink` kwarg on `LLMProvider.stream_chat`
    and forwarded to Langfuse generations for cost analytics.

    `total_tokens` is the upstream's sum, not `prompt + completion` —
    we trust the vendor to handle reasoning-token / cached-prompt
    accounting consistently with their own dashboards.
    """

    prompt_tokens: int
    completion_tokens: int
    total_tokens: int

    def to_langfuse_usage(self) -> dict[str, int]:
        """Langfuse v2's `generation(usage=...)` accepts this exact key set."""
        return {
            "input": self.prompt_tokens,
            "output": self.completion_tokens,
            "total": self.total_tokens,
        }
