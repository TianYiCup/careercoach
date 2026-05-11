"""Observability glue — Langfuse traces, structured logs, Sentry already in main.

Foundation §3.7.1 — every prompt → response must be traceable in
Langfuse. This package exposes a tiny surface so the API layer can
emit traces without depending on Langfuse internals directly.
"""

from app.observability.langfuse import (
    get_langfuse_client,
    run_session_turn,
)

__all__ = ["get_langfuse_client", "run_session_turn"]
