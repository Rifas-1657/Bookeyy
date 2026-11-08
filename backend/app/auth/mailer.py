"""
app/auth/mailer.py — send the password reset and email verification emails.

Brevo's HTTP API is the default: its free plan needs no domain, and Render's free
plan blocks outbound SMTP. SMTP stays as an option for other hosts.

  BREVO_API_KEY    Brevo key (free: 300 emails a day)
  MAIL_FROM        sender address, verified in Brevo
  PUBLIC_APP_URL   the site's address, used in the reset link
  SMTP_*           used when BREVO_API_KEY is unset
"""
from __future__ import annotations

import asyncio
import logging
import smtplib
from email.message import EmailMessage

import httpx

from app.config import get_settings

log = logging.getLogger(__name__)

SUBJECT = "Reset your Bookey password"
TEXT = """Hi {name},

Someone asked to reset your Bookey password. Open this link to choose a new one:

{link}

The link works once and expires soon. If this wasn't you, ignore this email and your password stays the same.

Bookey
"""


def reset_link(raw_token: str) -> str:
    # In the fragment, so the token never reaches server or analytics logs.
    return f"{get_settings().public_app_url.rstrip('/')}/reset-password#token={raw_token}"


VERIFY_SUBJECT = "Confirm your Bookey email"
VERIFY_TEXT = """Hi {name},

Welcome to Bookey. Please confirm this is your email address:

{link}

The link works for 3 days. If you didn't create a Bookey account, you can ignore this email.

Bookey
"""


def verify_link(token: str) -> str:
    return f"{get_settings().public_app_url.rstrip('/')}/verify-email#token={token}"


def _send_smtp(to: str, body: str, subject: str = SUBJECT) -> None:
    s = get_settings()
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = subject, s.mail_from, to
    msg.set_content(body)
    with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=15) as server:
        server.starttls()
        if s.smtp_user:
            server.login(s.smtp_user, s.smtp_password)
        server.send_message(msg)


async def _deliver(to: str, subject: str, body: str, what: str) -> None:
    s = get_settings()
    try:
        if s.brevo_api_key:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(
                    "https://api.brevo.com/v3/smtp/email",
                    headers={"api-key": s.brevo_api_key},
                    json={"sender": {"name": "Bookey", "email": s.mail_from}, "to": [{"email": to}],
                          "subject": subject, "textContent": body},
                )
            resp.raise_for_status()
        elif s.smtp_host:
            await asyncio.to_thread(_send_smtp, to, body, subject)
        else:
            log.error("%s email not sent: set BREVO_API_KEY and MAIL_FROM", what)
    except (httpx.HTTPError, OSError, smtplib.SMTPException) as exc:
        log.error("%s email failed: %s", what, type(exc).__name__)


async def send_password_reset(to: str, name: str, raw_token: str) -> None:
    """Runs after the response is sent, so timing never reveals whether an account exists."""
    body = TEXT.format(name=name or "there", link=reset_link(raw_token))
    await _deliver(to, SUBJECT, body, "Password reset")


async def send_email_verification(to: str, name: str, token: str) -> None:
    body = VERIFY_TEXT.format(name=name or "there", link=verify_link(token))
    await _deliver(to, VERIFY_SUBJECT, body, "Verification")
