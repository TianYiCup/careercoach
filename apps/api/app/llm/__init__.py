"""LLM provider abstraction layer.

Foundation §3.3.4 — every call to an external model goes through
`LLMProvider`. Adapters (DeepSeek, Qwen, ...) live in sibling modules
and the failover router composes them.
"""

from app.llm.deepseek import DeepSeekProvider
from app.llm.errors import (
    LLMAuthError,
    LLMError,
    LLMRateLimitError,
    LLMTimeoutError,
    LLMUpstreamError,
)
from app.llm.provider import LLMProvider
from app.llm.qwen import QwenProvider
from app.llm.router import LLMRouter
from app.llm.types import Message, Role, TokenUsage

__all__ = [
    "DeepSeekProvider",
    "LLMAuthError",
    "LLMError",
    "LLMProvider",
    "LLMRateLimitError",
    "LLMRouter",
    "LLMTimeoutError",
    "LLMUpstreamError",
    "Message",
    "QwenProvider",
    "Role",
    "TokenUsage",
]
