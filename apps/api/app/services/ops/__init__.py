"""Ops-side service package — A-41 ships the auth dep.

Public surface:
  * `require_ops_token` — FastAPI dependency that gates
    `/v1/ops/*` endpoints behind a static `X-Ops-Token` header.

A-42 will add the first ops endpoint (`/v1/ops/token-cost`) and
attach this dep. Subsequent ops endpoints reuse the same dep.
"""

from app.services.ops.dependency import require_ops_token

__all__ = ["require_ops_token"]
