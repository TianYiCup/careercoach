"""Tests for the email dispatcher PR-A1 (CareerCoach foundation §F1).

PR-A1 ships the dispatcher abstraction + the dev-only logging fallback
+ the SMTP-backed production dispatcher. Routes and `AuthService`
wiring land in PR-A2; nothing here exercises HTTP.
"""

from __future__ import annotations

from email.message import EmailMessage
from typing import Any

import pytest
from app.services.auth.email_dispatcher import (
    EmailDispatcher,
    LoggingEmailDispatcher,
    SmtpConfigError,
    SmtpEmailDispatcher,
    _build_message,
    _mask_email,
)
from structlog.testing import capture_logs


class TestMaskEmail:
    """`_mask_email` is the log-discipline helper — every dispatcher
    logs the *masked* address, never the raw one (PRD §6.2)."""

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("alex@example.com", "a***@example.com"),
            ("a@example.com", "*@example.com"),
            ("ab@example.com", "a*@example.com"),
            ("ALEXANDER@example.com", "A********@example.com"),
            ("user+tag@example.com", "u*******@example.com"),
        ],
    )
    def test_keeps_first_char_and_domain(self, raw: str, expected: str) -> None:
        assert _mask_email(raw) == expected

    def test_falls_back_to_stars_when_no_at_sign(self) -> None:
        assert _mask_email("not-an-email") == "***"

    def test_falls_back_to_stars_when_empty(self) -> None:
        assert _mask_email("") == "***"


class TestLoggingEmailDispatcher:
    """Dev-only fallback. Mirrors `LoggingDispatcher` for SMS."""

    def test_satisfies_protocol(self) -> None:
        dispatcher = LoggingEmailDispatcher()
        assert isinstance(dispatcher, EmailDispatcher)

    def test_has_logging_name(self) -> None:
        assert LoggingEmailDispatcher().name == "logging"

    @pytest.mark.asyncio
    async def test_send_logs_masked_email_and_code(self) -> None:
        dispatcher = LoggingEmailDispatcher()
        with capture_logs() as logs:
            await dispatcher.send(email="alex@example.com", code="123456")

        record = next(
            (entry for entry in logs if entry.get("event") == "email_dispatched_via_log"),
            None,
        )
        assert record is not None, "expected an email_dispatched_via_log entry"
        # masked recipient + code both present
        assert record["email"] == "a***@example.com"
        assert record["code"] == "123456"
        # raw email NEVER in the structured log
        flat = " ".join(f"{k}={v}" for k, v in record.items())
        assert "alex@example.com" not in flat


class _StubSmtpClient:
    """In-test capture for what `SmtpEmailDispatcher` hands to
    `aiosmtplib.send`. We assert against the captured kwargs rather
    than monkey-patching the network."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def __call__(self, message: EmailMessage, **kwargs: Any) -> None:
        self.calls.append({"message": message, **kwargs})


class TestBuildMessage:
    """The MIME message is the only thing the route can't easily
    inspect once the dispatcher is wired — so test it directly."""

    def test_sets_to_from_subject(self) -> None:
        msg = _build_message(
            recipient="alex@example.com",
            code="654321",
            sender="noreply@careercoach.app",
        )
        assert msg["To"] == "alex@example.com"
        assert msg["From"] == "noreply@careercoach.app"
        # Subject must hint that this is a verification code so the
        # user recognises it in their inbox preview.
        assert "验证码" in msg["Subject"] or "verification" in msg["Subject"].lower()

    def test_body_contains_code(self) -> None:
        msg = _build_message(
            recipient="alex@example.com",
            code="654321",
            sender="noreply@careercoach.app",
        )
        body = msg.get_content()
        assert "654321" in body

    def test_body_warns_dont_share(self) -> None:
        # PRD §6.2 — code is credential-grade, body must tell the user
        # not to share it. Keeps us aligned with bank-grade copy.
        msg = _build_message(
            recipient="alex@example.com",
            code="654321",
            sender="noreply@careercoach.app",
        )
        body = msg.get_content()
        assert "不要" in body or "do not" in body.lower()


class TestSmtpEmailDispatcher:
    """SMTP-backed dispatcher. Network is stubbed; we assert config
    handling + how the message is handed to the SMTP client."""

    def _ok_config(self) -> dict[str, Any]:
        return {
            "host": "smtp.resend.com",
            "port": 587,
            "username": "resend",
            "password": "re_test_apikey",
            "sender": "noreply@careercoach.app",
            "starttls": True,
        }

    def test_satisfies_protocol(self) -> None:
        dispatcher = SmtpEmailDispatcher(**self._ok_config())
        assert isinstance(dispatcher, EmailDispatcher)

    def test_name_is_smtp(self) -> None:
        assert SmtpEmailDispatcher(**self._ok_config()).name == "smtp"

    @pytest.mark.parametrize("missing", ["host", "username", "password", "sender"])
    def test_rejects_missing_required_field(self, missing: str) -> None:
        # Fail-fast at construction so a misconfigured deploy doesn't
        # reach the first send-call (where it would surface as a
        # 500 to the user) — matches the JWT_SECRET fail-fast pattern.
        config = self._ok_config()
        config[missing] = ""
        with pytest.raises(SmtpConfigError, match=missing):
            SmtpEmailDispatcher(**config)

    @pytest.mark.parametrize("bad_port", [0, -1, 70000])
    def test_rejects_invalid_port(self, bad_port: int) -> None:
        config = self._ok_config()
        config["port"] = bad_port
        with pytest.raises(SmtpConfigError, match="port"):
            SmtpEmailDispatcher(**config)

    @pytest.mark.asyncio
    async def test_send_invokes_smtp_with_message(self) -> None:
        stub = _StubSmtpClient()
        dispatcher = SmtpEmailDispatcher(**self._ok_config(), _smtp_send=stub)

        await dispatcher.send(email="alex@example.com", code="654321")

        assert len(stub.calls) == 1
        call = stub.calls[0]
        msg: EmailMessage = call["message"]
        assert msg["To"] == "alex@example.com"
        assert "654321" in msg.get_content()
        assert call["hostname"] == "smtp.resend.com"
        assert call["port"] == 587
        assert call["username"] == "resend"
        assert call["password"] == "re_test_apikey"
        assert call["start_tls"] is True

    @pytest.mark.asyncio
    async def test_send_logs_masked_only(self) -> None:
        stub = _StubSmtpClient()
        dispatcher = SmtpEmailDispatcher(**self._ok_config(), _smtp_send=stub)

        with capture_logs() as logs:
            await dispatcher.send(email="alex@example.com", code="654321")

        flat = " ".join(f"{k}={v}" for entry in logs for k, v in entry.items() if k != "log_level")
        assert "a***@example.com" in flat
        # Raw address never lands in the log, even on success.
        assert "alex@example.com" not in flat
        # And the code itself never leaks either — masked or omitted.
        # The dev-only LoggingEmailDispatcher logs the code (copy-paste
        # convenience); the production SMTP path must NOT.
        assert "654321" not in flat

    @pytest.mark.asyncio
    async def test_send_wraps_smtp_exception_as_runtime(self) -> None:
        async def _exploding_smtp(*_args: Any, **_kwargs: Any) -> None:
            raise ConnectionError("upstream smtp down")

        dispatcher = SmtpEmailDispatcher(**self._ok_config(), _smtp_send=_exploding_smtp)

        with pytest.raises(RuntimeError, match="email dispatch failed"):
            await dispatcher.send(email="alex@example.com", code="654321")
