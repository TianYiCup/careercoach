"""Application configuration loaded from environment."""

from functools import lru_cache
from typing import Literal, Self

import structlog
from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = structlog.get_logger(__name__)

AppEnv = Literal["development", "staging", "production"]

# Marker for the unsigned dev default. Production startup must NOT see
# this value — settings validation raises if it does.
DEV_JWT_SECRET = "dev-only-not-for-prod-32-chars-min!"  # noqa: S105 — sentinel, not a credential

# HS256 best practice is ≥ 32 bytes of secret material so a brute-force
# adversary doesn't compress the keyspace.
_MIN_JWT_SECRET_BYTES = 32


class Settings(BaseSettings):
    """Runtime settings sourced from environment variables.

    Loaded once per process via `get_settings()`. Never log raw values
    of fields ending in `_key` or `_secret`.
    """

    app_env: AppEnv = "development"
    app_port: int = 8000
    app_log_level: str = "info"

    # Comma-separated CORS allowlist. The default covers Vite dev
    # (`localhost:5173`), the FastAPI host itself (when EXE / web hit
    # via same host), and Tauri's webview origins. Production sets
    # `CORS_ALLOWED_ORIGINS` to the real frontend domain(s).
    #
    # Wildcard `*` is deliberately NOT supported here because we send
    # `Authorization` (Bearer JWT — hard auth is on) and the CORS spec
    # forbids credentialed wildcard. Be explicit.
    cors_allowed_origins: str = Field(
        default=(
            "http://localhost:5173,http://localhost:8000,tauri://localhost,https://tauri.localhost"
        ),
        description=(
            "Comma-separated list of origins permitted via CORS. "
            "Set CORS_ALLOWED_ORIGINS in prod to the frontend domain."
        ),
    )

    @property
    def cors_allowed_origins_list(self) -> list[str]:
        """Parsed view — split on comma, strip whitespace, drop blanks."""
        return [o.strip() for o in self.cors_allowed_origins.split(",") if o.strip()]

    database_url: str = Field(
        default="postgresql+asyncpg://postgres:postgres@localhost:5432/careercoach"
    )
    redis_url: str = Field(default="redis://localhost:6379/0")
    qdrant_url: str = Field(default="http://localhost:6333")

    sentry_dsn: str = ""

    # `SecretStr` so `repr(settings)` / debug logging never leaks the
    # raw value the way a plain `str` field would. The dev default
    # `DEV_JWT_SECRET` is OK for `app_env == "development"` only —
    # the cross-field validator below raises if it leaks into staging
    # or production.
    jwt_secret: SecretStr = Field(default=SecretStr(DEV_JWT_SECRET))

    # Sessions + turns persistence backend.
    # `memory` (default) keeps everything dict-backed inside the worker
    # so dev runs without docker still work. `postgres` switches to the
    # SQLAlchemy-backed impls; alembic must be `upgrade head` first.
    sessions_repo_backend: Literal["memory", "postgres"] = Field(default="memory")

    # Review (复盘师) upload + turn persistence — same defaulting rule as
    # sessions. A-9 ships the schema + repo; A-10/A-11 wire it to the
    # reviewer chain and the GET route.
    review_repo_backend: Literal["memory", "postgres"] = Field(default="memory")

    # Copilot (副驾) session persistence — same defaulting rule.
    # A-15 shipped the schema + repo + create_session route; A-16
    # shipped the WS endpoint that flips status via mark_connected /
    # mark_ended on this same repo.
    copilot_repo_backend: Literal["memory", "postgres"] = Field(default="memory")

    # Vibe (daily mood check-in) persistence — same defaulting rule.
    vibe_repo_backend: Literal["memory", "postgres"] = Field(default="memory")

    # Streak (consecutive practice-days) persistence — same defaulting rule.
    streak_repo_backend: Literal["memory", "postgres"] = Field(default="memory")

    # Weakness (communication-weakness profile) persistence — same rule.
    weakness_repo_backend: Literal["memory", "postgres"] = Field(default="memory")

    # LLM call (per-generation token accounting) persistence — same
    # defaulting rule. A-39 ships the schema + repo; A-40 wires the
    # observability `record_generation` callsites to actually insert.
    # `memory` is fine for dev/tests because the rollup endpoint
    # (A-41/A-42) is an ops-only surface — nothing user-facing breaks
    # when the backend has no history.
    llm_calls_repo_backend: Literal["memory", "postgres"] = Field(default="memory")

    # Moderation-event read-side repo (A-43). The write path is
    # `DbEventSink` which is unconditional Postgres in prod; this
    # flag only switches the *read* impl used by the ops tail
    # endpoint. Memory backend is for dev/tests where the ops
    # surface is exercised against in-memory seed data.
    moderation_events_repo_backend: Literal["memory", "postgres"] = Field(default="memory")

    # Static bearer-style token the `/v1/ops/*` endpoints check via
    # `X-Ops-Token` header. Empty (the default) means ops endpoints
    # are DISABLED in this deployment — the dep returns 503 rather
    # than allowing open access, so a forgotten env var fails closed.
    # A-41 ships only the dep; A-42 attaches it to the cost rollup.
    ops_api_token: SecretStr = Field(default=SecretStr(""))

    # Email-auth dispatcher (PR-A2). `logging` is dev-only — writes the
    # code to the app log so a developer can copy-paste. `smtp` ships
    # via Resend (smtp.resend.com:587) or any RFC-5321 SMTP server. The
    # wiring layer refuses to start with `logging` outside dev.
    auth_email_dispatcher_backend: Literal["logging", "smtp"] = Field(default="logging")

    # SMTP credentials. v0 ships against Resend
    # (host=smtp.resend.com, port=587, user=resend, password=<API key>).
    # Empty by default so dev runs without a real SMTP account stay on
    # the `logging` dispatcher. The factory raises at startup if the
    # backend is `smtp` and any of these are empty.
    smtp_host: str = Field(default="")
    smtp_port: int = Field(default=587, ge=1, le=65535)
    smtp_username: str = Field(default="")
    smtp_password: SecretStr = Field(default=SecretStr(""))
    smtp_from: str = Field(
        default="",
        description="Verified sender address shown to recipients (e.g. noreply@careercoach.app).",
    )
    smtp_starttls: bool = Field(default=True)

    # Base URL the client uses to open the copilot WebSocket. The
    # service appends `/v1/copilot/sessions/{copilot_id}/stream`. v0
    # dev runs against `ws://localhost:8000`; production sets this to
    # the realtime gateway hostname. A-15 ships the URL in the POST
    # response; A-16 shipped the actual endpoint at that path.
    copilot_ws_base_url: str = Field(
        default="ws://localhost:8000",
        description=(
            "Base URL for copilot WebSocket connections (no trailing slash). "
            "Service appends /v1/copilot/sessions/{copilot_id}/stream."
        ),
    )

    # ASR backend selection — `dummy` (UTF-8 echo) for tests + dev,
    # `aliyun` for the real NLS streaming ASR (A-28), `whisper_cpp`
    # for a self-hosted whisper.cpp HTTP server (US-B3 privacy path).
    # The factory refuses to construct an external backend with
    # missing credentials / URL so a misconfigured prod boot fails
    # loudly rather than silently falling back to a non-functional
    # dummy.
    asr_backend: Literal["dummy", "aliyun", "whisper_cpp"] = Field(
        default="dummy",
        description=(
            "ASR provider backend. `dummy` for dev/tests, `aliyun` for prod (requires "
            "ALIYUN_ACCESS_KEY_ID/SECRET + ALIYUN_ASR_APP_KEY), `whisper_cpp` for "
            "self-hosted whisper.cpp (requires WHISPER_CPP_BASE_URL)."
        ),
    )

    # whisper.cpp self-hosted HTTP server (US-B3 privacy path). Empty
    # default = unusable; the factory raises ValueError if asr_backend
    # is flipped to `whisper_cpp` without a URL set. The server is
    # expected to be the upstream `whisper-server` binary exposing
    # POST /inference with a multipart audio body.
    whisper_cpp_base_url: str = Field(
        default="",
        description=(
            "Base URL of the self-hosted whisper.cpp HTTP server "
            "(e.g. http://whisper.internal:8080). Path /inference is appended."
        ),
    )
    whisper_cpp_timeout_s: float = Field(
        default=8.0,
        description=(
            "Per-utterance budget end-to-end for whisper.cpp transcription "
            "(connect + upload + decode)."
        ),
    )

    # Auth user persistence — same defaulting rule.
    auth_repo_backend: Literal["memory", "postgres"] = Field(default="memory")

    # SMS code store — Redis is the right home (5-min TTL + atomic
    # GETDEL for one-shot verification). Defaults to in-memory so dev
    # runs without docker still work; production should set this to
    # `redis` so codes survive a restart and parallel verifies can't
    # replay the same code.
    auth_code_store_backend: Literal["memory", "redis"] = Field(default="memory")

    # Sharecards score-cache persistence. Set to `postgres` so a
    # restart doesn't drop the score-card render cache for previously
    # ended sessions (otherwise `/sharecards/session/{id}` 404s on
    # sessions that ended pre-restart even though the LLM summary
    # already cost money to produce).
    sharecards_score_repo_backend: Literal["memory", "postgres"] = Field(default="memory")

    # LLM · DeepSeek (primary, OpenAI-compatible).
    # Empty key means the adapter will refuse to send requests; only the
    # router knows how to fall back when this happens.
    deepseek_api_key: SecretStr = Field(default=SecretStr(""))
    deepseek_base_url: str = Field(default="https://api.deepseek.com")
    deepseek_model: str = Field(default="deepseek-chat")

    # LLM · Qwen / DashScope (backup, OpenAI-compatible mode).
    # DashScope hosts the OpenAI-compatible endpoint under
    # /compatible-mode/v1 — keep the trailing /compatible-mode here
    # since the adapter appends /v1/chat/completions.
    qwen_api_key: SecretStr = Field(default=SecretStr(""))
    qwen_base_url: str = Field(default="https://dashscope.aliyuncs.com/compatible-mode")
    qwen_model: str = Field(default="qwen-max")

    # Langfuse · LLM trace observability (foundation §3.7.1).
    # Empty keys mean trace is disabled — `get_langfuse_client` returns
    # None and callers skip instrumentation, so dev runs without a
    # Langfuse instance still work.
    langfuse_public_key: SecretStr = Field(default=SecretStr(""))
    langfuse_secret_key: SecretStr = Field(default=SecretStr(""))
    langfuse_host: str = Field(default="http://localhost:3001")

    # Aliyun Content Moderation 2.0 — primary moderation backend when keys
    # are configured. Empty AK/secret means we skip the cascade and the
    # moderation service falls back to the bundled local dict.
    aliyun_access_key_id: SecretStr = Field(default=SecretStr(""))
    aliyun_access_key_secret: SecretStr = Field(default=SecretStr(""))
    aliyun_moderation_endpoint: str = Field(
        default="green-cip.cn-shanghai.aliyuncs.com",
        description="Aliyun Green-CIP host. Region prefix follows aliyun pop names.",
    )
    aliyun_moderation_service: str = Field(
        default="chat_detection_pro",
        description="Aliyun service scene id — `chat_detection_pro` covers IM/UGC.",
    )
    aliyun_moderation_timeout_s: float = Field(
        default=0.8,
        description="Per-request budget. CascadingBackend falls back to local dict on timeout.",
    )

    # Aliyun NLS streaming ASR (A-28). Reuses the shared AK/secret
    # above. AppKey is project-specific (registered per app on the
    # Aliyun console). Endpoint defaults to Shanghai because that's
    # the lowest-latency region from our planned deploy zone.
    aliyun_asr_app_key: SecretStr = Field(
        default=SecretStr(""),
        description="Aliyun NLS app key (per-project, separate from the account-level AK).",
    )
    aliyun_asr_ws_url: str = Field(
        default="wss://nls-gateway-cn-shanghai.aliyuncs.com/ws/v1",
        description="Aliyun NLS realtime streaming endpoint.",
    )
    aliyun_asr_token_url: str = Field(
        default="https://nls-meta.cn-shanghai.aliyuncs.com/",
        description="Aliyun NLS access-token issuer (STS-style, HMAC-SHA1 signed).",
    )
    aliyun_asr_timeout_s: float = Field(
        default=8.0,
        description="Per-utterance budget end-to-end (connect + stream + final).",
    )

    # TTS backend selection — `dummy` (synthetic WAV) for dev/tests,
    # `edge` for Microsoft Edge TTS over WSS (foundation §3.4.2, no
    # key required), `aliyun` for the NLS streaming TTS backup.
    # Default `dummy` so tests run offline; production sets `edge`.
    tts_backend: Literal["dummy", "edge", "aliyun"] = Field(
        default="dummy",
        description=(
            "TTS provider backend. `dummy` for dev/tests, `edge` for Microsoft "
            "Edge TTS (no key), `aliyun` for Aliyun NLS streaming TTS."
        ),
    )

    # Aliyun NLS streaming TTS — same AK/secret as ASR + moderation;
    # AppKey is project-specific (separate from `aliyun_asr_app_key`,
    # registered per app on the Aliyun console).
    aliyun_tts_app_key: SecretStr = Field(
        default=SecretStr(""),
        description="Aliyun NLS TTS app key (per-project, separate from the ASR AppKey).",
    )
    aliyun_tts_ws_url: str = Field(
        default="wss://nls-gateway-cn-shanghai.aliyuncs.com/ws/v1",
        description="Aliyun NLS realtime streaming endpoint (TTS shares the ASR gateway).",
    )
    aliyun_tts_timeout_s: float = Field(
        default=8.0,
        description="Per-synthesis budget end-to-end (connect + stream + final).",
    )

    # Share-card storage. `dir` is where the LocalFilesystemStorage drops
    # PNGs; `public_base_url` is the prefix the frontend hits to fetch
    # them. v0 dev runs both on the same FastAPI process; prod swaps
    # the storage backend for S3/OSS without changing the URL contract.
    sharecards_storage_dir: str = Field(
        default="./var/sharecards",
        description="Local dir for share-card PNGs. Created on startup.",
    )
    sharecards_public_base_url: str = Field(
        default="http://localhost:8000/static/sharecards",
        description="URL prefix returned in ShareCardResponse.png_url + share_links.save_local.",
    )
    sharecards_app_origin: str = Field(
        default="https://careercoach.app",
        description="Origin used inside QR codes + share-link deep paths (e.g. /share/{card_id}).",
    )

    # Demo-mode bypass for judge-facing trials (PR-D1). When ON, the
    # SMS + email verify routes accept ANY 6-digit code so a judge can
    # log in with `000000` instead of waiting for a real code. Rate
    # limits are still enforced server-side at the dispatcher layer
    # (the bypass only skips the code-match step). MUST be off in
    # staging/production — the model-validator below raises if you try.
    demo_mode: bool = Field(
        default=False,
        description=(
            "Accept any 6-digit code on /auth/{sms,email}/verify. "
            "Dev-only — production/staging refuse to start with this on."
        ),
    )

    model_config = SettingsConfigDict(
        env_file=("../../.env", ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    @model_validator(mode="after")
    def _validate_jwt_secret(self) -> Self:
        """Fail-fast on a startup-time misconfiguration of the signing
        secret. Catches three classes of mistake:

          1. Production / staging deploy that forgot to set
             `JWT_SECRET` — would otherwise sign tokens with the
             public dev default, letting anyone mint valid JWTs.
          2. Any environment with a too-short secret (HS256
             best-practice is ≥ 32 bytes).
          3. Dev runs with the canonical default — log a WARNING so
             nobody mistakes it for a real secret on their screen.
        """
        raw = self.jwt_secret.get_secret_value()

        if len(raw.encode("utf-8")) < _MIN_JWT_SECRET_BYTES:
            raise ValueError(
                f"jwt_secret must be at least {_MIN_JWT_SECRET_BYTES} bytes "
                f"(got {len(raw.encode('utf-8'))} bytes). HS256 keys shorter "
                "than 32 bytes weaken the signature meaningfully."
            )

        if raw == DEV_JWT_SECRET:
            if self.app_env != "development":
                raise ValueError(
                    "jwt_secret is set to the public dev default in a "
                    f"non-dev env (app_env={self.app_env}). Set the "
                    "JWT_SECRET environment variable to a unique value "
                    "≥ 32 bytes before starting the server."
                )
            # Dev: allowed, but make it visible so it never gets shipped
            # by accident.
            logger.warning(
                "jwt_secret_is_dev_default",
                msg="using DEV_JWT_SECRET; do NOT deploy with this value",
            )

        return self

    @model_validator(mode="after")
    def _validate_demo_mode(self) -> Self:
        """Fail-fast on a misconfigured demo-mode deploy.

        `demo_mode=True` makes /auth/{sms,email}/verify accept any
        six-digit code. That is exactly the right thing for a judge
        trying the app from their phone during a pitch, and exactly
        the wrong thing in a real shared environment — anyone with
        a phone number could mint a JWT for any other user.

        Refusing the combination at startup mirrors the SMS-dispatcher
        guard and the JWT-secret validator: a deploy that forgot to
        flip DEMO_MODE=false fails to boot instead of shipping a
        gaping hole.
        """
        if self.demo_mode and self.app_env != "development":
            raise ValueError(
                "demo_mode=True is only allowed when app_env=development. "
                f"Got app_env={self.app_env}. Unset DEMO_MODE (or set it "
                "to false) before deploying outside dev — the bypass lets "
                "any caller mint a JWT for any phone/email."
            )
        if self.demo_mode:
            logger.warning(
                "demo_mode_enabled",
                msg="DEMO_MODE on; /auth/*/verify accepts any 6-digit code",
            )
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
