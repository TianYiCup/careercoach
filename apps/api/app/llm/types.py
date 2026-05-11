"""Provider-agnostic message types passed to `LLMProvider.stream_chat`.

Kept deliberately small: only what every adapter needs. Anything
provider-specific (function calls, image parts, etc.) belongs in the
adapter module, not here.
"""

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
