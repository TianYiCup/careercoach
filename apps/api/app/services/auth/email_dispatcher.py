"""Email-based verification-code dispatcher (CareerCoach foundation §F1).

The SMS dispatcher in `service.py` ships our v0 phone-auth path; this
module is the parallel email path, added under time-pressure when ICP
备案 + SMS-template review pushed the SMS go-live out by weeks.

Public surface
--------------
* :class:`EmailDispatcher` — runtime-checkable Protocol the auth wiring
  depends on. Two implementations live here:
* :class:`LoggingEmailDispatcher` — dev-only. Writes the code into the
  app log so a developer can copy-paste it. Refused outside
  ``app_env == "development"`` by the wiring layer (PR-A2).
* :class:`SmtpEmailDispatcher` — production-grade SMTP send. Works with
  any SMTP server; v0 targets Resend (``smtp.resend.com:587``) because
  it ships in five minutes with no DNS gymnastics. ``aiosmtplib.send``
  is injectable so tests don't open a socket.

Logging rules
-------------
* Recipient is ALWAYS masked via :func:`_mask_email`. The raw address
  never enters a log line (PRD §6.2 / NFR S-05).
* The dev :class:`LoggingEmailDispatcher` *does* log the code — that's
  the whole point of it. The production SMTP path must NOT.

Wiring
------
This file does not wire anything. PR-A2 adds ``get_email_auth_service``
+ ``/v1/auth/email/{send,verify}`` routes and folds the env-based
selection there.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from email.message import EmailMessage
from typing import Any, Protocol, runtime_checkable

import structlog

logger = structlog.get_logger(__name__)


@runtime_checkable
class EmailDispatcher(Protocol):
    """Pluggable email gateway. Mirrors `SmsDispatcher` in
    `service.py` so the wiring layer can swap implementations
    without touching `AuthService`."""

    name: str

    async def send(self, *, email: str, code: str) -> None: ...


class SmtpConfigError(ValueError):
    """Construction-time failure for :class:`SmtpEmailDispatcher`.

    Raised when a required SMTP field is empty or the port is outside
    the valid TCP range. Surfaces at wiring time (PR-A2) so a deploy
    that forgot a secret fails fast — same pattern as the JWT_SECRET
    validator in :mod:`app.config`.
    """


class LoggingEmailDispatcher:
    """Dev-only no-op dispatcher. Writes the code to the log so a
    developer can copy-paste it from stdout.

    NEVER ship outside ``app_env == "development"`` — verification
    codes in plaintext in app logs are an audit failure (PRD §6.2 /
    NFR S-05). The wiring layer (PR-A2) refuses to construct this
    outside dev, mirroring the SMS `LoggingDispatcher` guard."""

    name: str = "logging"

    async def send(self, *, email: str, code: str) -> None:
        logger.info(
            "email_dispatched_via_log",
            email=_mask_email(email),
            code=code,
            note="dev-only dispatcher; do not use in production",
        )


SmtpSendCallable = Callable[..., Awaitable[None]]


class SmtpEmailDispatcher:
    """SMTP-backed dispatcher. Works with any RFC-5321 SMTP server;
    targets Resend (``smtp.resend.com:587``) in v0 because it onboards
    fastest. ``aiosmtplib.send`` is injectable as ``_smtp_send`` so
    tests don't have to stub the socket layer.
    """

    name: str = "smtp"

    def __init__(
        self,
        *,
        host: str,
        port: int,
        username: str,
        password: str,
        sender: str,
        starttls: bool = True,
        _smtp_send: SmtpSendCallable | None = None,
    ) -> None:
        for field_name, value in (
            ("host", host),
            ("username", username),
            ("password", password),
            ("sender", sender),
        ):
            if not value:
                raise SmtpConfigError(
                    f"SMTP dispatcher misconfigured: '{field_name}' is empty. "
                    "Set SMTP_HOST / SMTP_USER / SMTP_PASSWORD / SMTP_FROM "
                    "in the environment before starting the server."
                )
        if not 1 <= port <= 65535:
            raise SmtpConfigError(
                f"SMTP dispatcher misconfigured: port={port} is outside the "
                "valid TCP range (1-65535)."
            )

        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._sender = sender
        self._starttls = starttls
        self._smtp_send = _smtp_send if _smtp_send is not None else _lazy_aiosmtplib_send

    async def send(self, *, email: str, code: str) -> None:
        message = _build_message(recipient=email, code=code, sender=self._sender)
        masked = _mask_email(email)

        try:
            await self._smtp_send(
                message,
                hostname=self._host,
                port=self._port,
                username=self._username,
                password=self._password,
                start_tls=self._starttls,
            )
        except Exception as exc:
            # Wrap so the route layer surfaces a single failure kind
            # rather than leaking aiosmtplib's exception hierarchy
            # into the HTTP response.
            logger.warning(
                "email_dispatch_failed",
                email=masked,
                provider=self._host,
                error_type=type(exc).__name__,
            )
            raise RuntimeError("email dispatch failed") from exc

        logger.info(
            "email_dispatched_via_smtp",
            email=masked,
            provider=self._host,
        )


def _build_message(*, recipient: str, code: str, sender: str) -> EmailMessage:
    """Construct the verification-code MIME message.

    Subject + body copy is deliberately bilingual (Chinese first,
    English fallback) so an inbox preview is recognisable even when
    the client falls back to Latin-1 rendering. Body keeps the
    "do-not-share" warning per PRD §6.2.
    """
    msg = EmailMessage()
    msg["Subject"] = f"CareerCoach 验证码 {code}"
    msg["From"] = sender
    msg["To"] = recipient
    msg.set_content(
        "你好，\n"
        "\n"
        f"你的 CareerCoach 验证码是：{code}\n"
        "\n"
        "5 分钟内有效。请不要把验证码发送给任何人，包括自称是客服的人。\n"
        "如果不是你本人请求，可以忽略这封邮件。\n"
        "\n"
        "—— CareerCoach 教练 K\n"
        "\n"
        "---\n"
        f"Your CareerCoach verification code: {code}\n"
        "Valid for 5 minutes. Do not share it with anyone.\n"
    )
    return msg


def _mask_email(raw: str) -> str:
    """Mask the local part of an email — log discipline (PRD §6.2).

    ``alex@example.com`` -> ``a***@example.com``
    ``a@example.com``    -> ``*@example.com``
    Returns ``***`` for inputs without an ``@`` so a malformed string
    can never accidentally land in a log line verbatim.
    """
    if "@" not in raw or not raw:
        return "***"
    local, _, domain = raw.partition("@")
    if not local:
        return f"*@{domain}"
    if len(local) == 1:
        return f"*@{domain}"
    return f"{local[0]}{'*' * (len(local) - 1)}@{domain}"


async def _lazy_aiosmtplib_send(message: EmailMessage, **kwargs: Any) -> None:
    """Default ``_smtp_send`` — imports ``aiosmtplib`` lazily so dev
    runs without the dependency installed (e.g. when only the
    LoggingEmailDispatcher path is exercised) don't blow up at
    import time."""
    import aiosmtplib

    await aiosmtplib.send(message, **kwargs)


__all__ = [
    "EmailDispatcher",
    "LoggingEmailDispatcher",
    "SmtpConfigError",
    "SmtpEmailDispatcher",
]
