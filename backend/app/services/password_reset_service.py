"""Password reset token lifecycle helpers."""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.password_reset_token import PasswordResetToken
from app.models.system_settings import SystemSetting


def _hash_token(token: str) -> str:
    """Hash a raw reset token before persistence or lookup."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


async def create_password_reset_token(db: AsyncSession, user_id: uuid.UUID) -> tuple[str, datetime]:
    """Create a new single-use token and invalidate older unused tokens."""
    now = datetime.now(timezone.utc)
    await db.execute(
        update(PasswordResetToken)
        .where(PasswordResetToken.user_id == user_id, PasswordResetToken.used_at.is_(None))
        .values(used_at=now)
    )

    raw_token = secrets.token_urlsafe(32)
    expires_at = now + timedelta(minutes=get_settings().PASSWORD_RESET_TOKEN_EXPIRE_MINUTES)
    db.add(
        PasswordResetToken(
            user_id=user_id,
            token_hash=_hash_token(raw_token),
            expires_at=expires_at,
        )
    )
    await db.flush()
    return raw_token, expires_at


async def get_public_base_url(db: AsyncSession) -> str:
    """Resolve the public base URL used for user-facing links."""
    result = await db.execute(select(SystemSetting).where(SystemSetting.key == "platform"))
    setting = result.scalar_one_or_none()
    if setting and setting.value and setting.value.get("public_base_url"):
        return str(setting.value["public_base_url"]).strip().rstrip("/")

    env_value = getattr(get_settings(), "PUBLIC_BASE_URL", "") if hasattr(get_settings(), "PUBLIC_BASE_URL") else ""
    env_value = str(env_value).strip().rstrip("/")
    if env_value:
        return env_value

    raise RuntimeError("Public base URL is not configured.")


async def build_password_reset_url(db: AsyncSession, raw_token: str) -> str:
    """Build the user-facing reset URL."""
    base_url = await get_public_base_url(db)
    return f"{base_url}/reset-password?token={raw_token}"


async def consume_password_reset_token(db: AsyncSession, raw_token: str) -> PasswordResetToken | None:
    """Load a valid reset token and mark it used."""
    now = datetime.now(timezone.utc)
    result = await db.execute(
        select(PasswordResetToken).where(PasswordResetToken.token_hash == _hash_token(raw_token))
    )
    token = result.scalar_one_or_none()
    if not token or token.used_at or token.expires_at <= now:
        return None

    token.used_at = now
    await db.flush()
    return token
