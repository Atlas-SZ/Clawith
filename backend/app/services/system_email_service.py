"""System-owned outbound email service."""

from __future__ import annotations

import smtplib
import ssl
from dataclasses import dataclass
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr, make_msgid

from app.config import get_settings
from app.services.email_service import _force_ipv4


class SystemEmailConfigError(RuntimeError):
    """Raised when system email configuration is missing or invalid."""


@dataclass(slots=True)
class SystemEmailConfig:
    """Resolved system email configuration."""

    from_address: str
    from_name: str
    smtp_host: str
    smtp_port: int
    smtp_username: str
    smtp_password: str
    smtp_ssl: bool


def get_system_email_config() -> SystemEmailConfig:
    """Resolve and validate the env-driven system email configuration."""
    settings = get_settings()
    from_address = settings.SYSTEM_EMAIL_FROM_ADDRESS.strip()
    smtp_host = settings.SYSTEM_SMTP_HOST.strip()
    smtp_username = settings.SYSTEM_SMTP_USERNAME.strip() or from_address
    smtp_password = settings.SYSTEM_SMTP_PASSWORD

    if not from_address or not smtp_host or not smtp_password:
        raise SystemEmailConfigError(
            "System email is not configured. Set SYSTEM_EMAIL_FROM_ADDRESS, SYSTEM_SMTP_HOST, and SYSTEM_SMTP_PASSWORD."
        )

    return SystemEmailConfig(
        from_address=from_address,
        from_name=settings.SYSTEM_EMAIL_FROM_NAME.strip() or "Clawith",
        smtp_host=smtp_host,
        smtp_port=settings.SYSTEM_SMTP_PORT,
        smtp_username=smtp_username,
        smtp_password=smtp_password,
        smtp_ssl=settings.SYSTEM_SMTP_SSL,
    )


async def send_system_email(to: str, subject: str, body: str) -> None:
    """Send a plain-text system email."""
    config = get_system_email_config()

    msg = MIMEMultipart()
    msg["From"] = formataddr((config.from_name, config.from_address))
    msg["To"] = to
    msg["Subject"] = subject
    msg["Message-ID"] = make_msgid()
    msg["Date"] = datetime.now().strftime("%a, %d %b %Y %H:%M:%S %z")
    msg.attach(MIMEText(body, "plain", "utf-8"))

    with _force_ipv4():
        if config.smtp_ssl:
            context = ssl.create_default_context()
            with smtplib.SMTP_SSL(config.smtp_host, config.smtp_port, context=context, timeout=15) as server:
                server.login(config.smtp_username, config.smtp_password)
                server.sendmail(config.from_address, [to], msg.as_string())
        else:
            with smtplib.SMTP(config.smtp_host, config.smtp_port, timeout=15) as server:
                server.ehlo()
                server.starttls(context=ssl.create_default_context())
                server.ehlo()
                server.login(config.smtp_username, config.smtp_password)
                server.sendmail(config.from_address, [to], msg.as_string())
