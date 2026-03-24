import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.background import BackgroundTasks

from app.api import auth as auth_api
from app.api import enterprise as enterprise_api
from app.api.notification import BroadcastRequest, broadcast_notification
from app.core.security import verify_password
from app.models.org import OrgDepartment, OrgMember
from app.models.password_reset_token import PasswordResetToken
from app.models.user import User
from app.schemas.schemas import ForgotPasswordRequest, ResetPasswordRequest
from app.services import org_sync_service, password_reset_service
from app.services.system_email_service import SystemEmailConfigError


class DummyScalars:
    def __init__(self, values):
        self._values = list(values)

    def all(self):
        return list(self._values)


class DummyResult:
    def __init__(self, value=None, values=None):
        self._value = value
        self._values = list(values or [])

    def scalar_one_or_none(self):
        return self._value

    def scalars(self):
        return DummyScalars(self._values)


class RecordingDB:
    def __init__(self, responses=None):
        self.responses = list(responses or [])
        self.executed = []
        self.added = []
        self.flushed = False
        self.committed = False

    async def execute(self, statement):
        self.executed.append(statement)
        if self.responses:
            return self.responses.pop(0)
        return DummyResult()

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        self.flushed = True

    async def commit(self):
        self.committed = True


def make_user(**overrides):
    values = {
        "id": uuid.uuid4(),
        "username": "alice",
        "email": "alice@example.com",
        "password_hash": "old-hash",
        "display_name": "Alice",
        "role": "member",
        "tenant_id": uuid.uuid4(),
        "is_active": True,
    }
    values.update(overrides)
    return User(**values)


@pytest.mark.asyncio
async def test_create_password_reset_token_invalidates_older_tokens(monkeypatch):
    monkeypatch.setattr(
        password_reset_service,
        "get_settings",
        lambda: SimpleNamespace(PASSWORD_RESET_TOKEN_EXPIRE_MINUTES=15, PUBLIC_BASE_URL=""),
    )
    db = RecordingDB()
    user_id = uuid.uuid4()

    raw_token, expires_at = await password_reset_service.create_password_reset_token(db, user_id)

    assert db.flushed is True
    assert len(db.executed) == 1
    assert "UPDATE password_reset_tokens" in str(db.executed[0])
    assert len(db.added) == 1
    saved_token = db.added[0]
    assert isinstance(saved_token, PasswordResetToken)
    assert saved_token.user_id == user_id
    assert saved_token.token_hash != raw_token
    assert len(raw_token) >= 20
    assert expires_at > datetime.now(timezone.utc)


@pytest.mark.asyncio
async def test_build_password_reset_url_uses_env_public_base_url(monkeypatch):
    monkeypatch.setattr(
        password_reset_service,
        "get_settings",
        lambda: SimpleNamespace(PASSWORD_RESET_TOKEN_EXPIRE_MINUTES=30, PUBLIC_BASE_URL="https://app.example.com/"),
    )
    db = RecordingDB([DummyResult(None)])

    url = await password_reset_service.build_password_reset_url(db, "abc123")

    assert url == "https://app.example.com/reset-password?token=abc123"


@pytest.mark.asyncio
async def test_consume_password_reset_token_rejects_expired_tokens():
    expired = PasswordResetToken(
        user_id=uuid.uuid4(),
        token_hash="hashed",
        expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
    )
    db = RecordingDB([DummyResult(expired)])

    token = await password_reset_service.consume_password_reset_token(db, "raw-token")

    assert token is None
    assert expired.used_at is None


@pytest.mark.asyncio
async def test_forgot_password_returns_generic_response_for_unknown_email():
    db = RecordingDB([DummyResult(None)])
    background_tasks = BackgroundTasks()

    response = await auth_api.forgot_password(
        ForgotPasswordRequest(email="missing@example.com"),
        background_tasks,
        db,
    )

    assert response == {
        "ok": True,
        "message": "If an account with that email exists, a password reset email has been sent.",
    }
    assert background_tasks.tasks == []


@pytest.mark.asyncio
async def test_forgot_password_hides_email_delivery_failures(monkeypatch):
    user = make_user()
    db = RecordingDB([DummyResult(user)])
    background_tasks = BackgroundTasks()

    def fake_get_system_email_config():
        raise RuntimeError("smtp failed")

    monkeypatch.setattr("app.services.system_email_service.get_system_email_config", fake_get_system_email_config)

    response = await auth_api.forgot_password(ForgotPasswordRequest(email=user.email), background_tasks, db)

    assert response["ok"] is True
    assert "password reset email" in response["message"]
    assert background_tasks.tasks == []


@pytest.mark.asyncio
async def test_forgot_password_queues_background_email(monkeypatch):
    user = make_user()
    db = RecordingDB([DummyResult(user)])
    background_tasks = BackgroundTasks()

    async def fake_create_password_reset_token(*_args, **_kwargs):
        return "raw-token", datetime.now(timezone.utc) + timedelta(minutes=30)

    async def fake_build_password_reset_url(*_args, **_kwargs):
        return "https://app.example.com/reset-password?token=raw-token"

    monkeypatch.setattr(password_reset_service, "create_password_reset_token", fake_create_password_reset_token)
    monkeypatch.setattr(password_reset_service, "build_password_reset_url", fake_build_password_reset_url)
    monkeypatch.setattr(
        "app.services.system_email_service.get_system_email_config",
        lambda: SimpleNamespace(from_address="bot@example.com"),
    )

    response = await auth_api.forgot_password(ForgotPasswordRequest(email=user.email), background_tasks, db)

    assert response["ok"] is True
    assert db.committed is True
    assert len(background_tasks.tasks) == 1


@pytest.mark.asyncio
async def test_reset_password_updates_user_and_invalidates_other_tokens(monkeypatch):
    user = make_user(password_hash=auth_api.hash_password("old-password"))
    consumed = PasswordResetToken(
        id=uuid.uuid4(),
        user_id=user.id,
        token_hash="current",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=30),
    )
    older = PasswordResetToken(
        id=uuid.uuid4(),
        user_id=user.id,
        token_hash="older",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=30),
    )
    db = RecordingDB([DummyResult(user), DummyResult(values=[consumed, older])])

    async def fake_consume_password_reset_token(*_args, **_kwargs):
        return consumed

    monkeypatch.setattr(password_reset_service, "consume_password_reset_token", fake_consume_password_reset_token)

    response = await auth_api.reset_password(
        ResetPasswordRequest(token="t" * 20, new_password="new-password"),
        db,
    )

    assert response == {"ok": True}
    assert verify_password("new-password", user.password_hash)
    assert older.used_at is not None
    assert db.flushed is True


@pytest.mark.asyncio
async def test_broadcast_notification_rejects_missing_system_email_config(monkeypatch):
    current_user = make_user(role="org_admin")

    def fake_get_system_email_config():
        raise SystemEmailConfigError("missing smtp host")

    monkeypatch.setattr(
        "app.services.system_email_service.get_system_email_config",
        fake_get_system_email_config,
    )

    with pytest.raises(HTTPException) as excinfo:
        await broadcast_notification(
            BroadcastRequest(title="Maintenance", body="Tonight", send_email=True),
            background_tasks=BackgroundTasks(),
            current_user=current_user,
            db=RecordingDB(),
        )

    assert excinfo.value.status_code == 400
    assert "System email is not configured" in excinfo.value.detail


@pytest.mark.asyncio
async def test_broadcast_notification_queues_email_delivery(monkeypatch):
    current_user = make_user(role="org_admin")
    target_user = make_user(email="bob@example.com", tenant_id=current_user.tenant_id)
    db = RecordingDB([
        DummyResult(values=[target_user]),
        DummyResult(values=[]),
    ])
    background_tasks = BackgroundTasks()

    monkeypatch.setattr(
        "app.services.system_email_service.get_system_email_config",
        lambda: SimpleNamespace(from_address="bot@example.com"),
    )
    notifications = []

    async def fake_send_notification(*_args, **kwargs):
        notifications.append(kwargs)

    monkeypatch.setattr("app.services.notification_service.send_notification", fake_send_notification)

    response = await broadcast_notification(
        BroadcastRequest(title="Maintenance", body="Tonight", send_email=True),
        background_tasks=background_tasks,
        current_user=current_user,
        db=db,
    )

    assert response["ok"] is True
    assert response["emails_sent"] == 1
    assert db.committed is True


@pytest.mark.asyncio
async def test_org_sync_public_config_falls_back_to_legacy_feishu_setting():
    legacy_setting = SimpleNamespace(
        value={
            "app_id": "cli_123",
            "app_secret": "legacy-secret",
            "last_synced_at": "2026-03-24T10:00:00+00:00",
        }
    )
    db = RecordingDB([DummyResult(None), DummyResult(legacy_setting)])

    value = await org_sync_service.org_sync_service.get_public_config(db)

    assert value["provider"] == "feishu"
    assert value["feishu"]["app_id"] == "cli_123"
    assert value["feishu"]["app_secret"] == ""
    assert value["feishu"]["last_synced_at"] == "2026-03-24T10:00:00+00:00"
    assert value["wecom"]["corp_id"] == ""


@pytest.mark.asyncio
async def test_org_sync_public_config_redacts_provider_secrets():
    stored_setting = SimpleNamespace(
        value={
            "provider": "wecom",
            "feishu": {"app_id": "cli_123", "app_secret": "keep-feishu", "last_synced_at": None},
            "wecom": {"corp_id": "ww123", "corp_secret": "keep-wecom", "last_synced_at": None},
        }
    )
    db = RecordingDB([DummyResult(stored_setting)])

    value = await org_sync_service.org_sync_service.get_public_config(db)

    assert value["feishu"]["app_secret"] == ""
    assert value["wecom"]["corp_secret"] == ""


@pytest.mark.asyncio
async def test_org_sync_save_config_preserves_existing_provider_secrets():
    existing_setting = SimpleNamespace(
        key="org_sync",
        value={
            "provider": "wecom",
            "feishu": {"app_id": "cli_123", "app_secret": "keep-feishu", "last_synced_at": None},
            "wecom": {"corp_id": "ww123", "corp_secret": "keep-wecom", "last_synced_at": None},
        },
    )
    db = RecordingDB([DummyResult(existing_setting), DummyResult(existing_setting)])

    saved = await org_sync_service.org_sync_service.save_config(
        db,
        {
            "provider": "wecom",
            "feishu": {"app_id": "cli_456", "app_secret": ""},
            "wecom": {"corp_id": "ww456", "corp_secret": ""},
        },
    )

    assert saved["feishu"]["app_secret"] == "keep-feishu"
    assert saved["wecom"]["corp_secret"] == "keep-wecom"
    assert existing_setting.value["feishu"]["app_id"] == "cli_456"
    assert existing_setting.value["wecom"]["corp_id"] == "ww456"
    assert db.committed is True


@pytest.mark.asyncio
async def test_get_org_sync_setting_requires_admin():
    with pytest.raises(HTTPException) as excinfo:
        await enterprise_api.get_system_setting(
            key="org_sync",
            current_user=make_user(role="member"),
            db=RecordingDB(),
        )

    assert excinfo.value.status_code == 403
    assert excinfo.value.detail == "Admin access required"


@pytest.mark.asyncio
async def test_list_org_departments_filters_by_active_provider(monkeypatch):
    wecom_dept = OrgDepartment(
        wecom_id="2",
        sync_provider="wecom",
        name="Engineering",
        member_count=3,
    )
    db = RecordingDB([DummyResult(values=[wecom_dept])])

    async def fake_get_active_provider(_db):
        return "wecom", {"corp_id": "ww123"}, {"provider": "wecom"}

    monkeypatch.setattr(org_sync_service.org_sync_service, "get_active_provider", fake_get_active_provider)

    rows = await enterprise_api.list_org_departments(
        tenant_id=None,
        current_user=make_user(),
        db=db,
    )

    assert rows == [
        {
            "id": str(wecom_dept.id),
            "provider": "wecom",
            "feishu_id": None,
            "wecom_id": "2",
            "name": "Engineering",
            "parent_id": None,
            "path": None,
            "member_count": 3,
        }
    ]
    assert "sync_provider" in str(db.executed[0])


@pytest.mark.asyncio
async def test_list_org_members_filters_by_active_provider(monkeypatch):
    wecom_member = OrgMember(
        wecom_user_id="zhangsan",
        sync_provider="wecom",
        name="张三",
        email="zhangsan@example.com",
        title="Engineer",
        department_path="Root / Engineering",
    )
    db = RecordingDB([DummyResult(values=[wecom_member])])

    async def fake_get_active_provider(_db):
        return "wecom", {"corp_id": "ww123"}, {"provider": "wecom"}

    monkeypatch.setattr(org_sync_service.org_sync_service, "get_active_provider", fake_get_active_provider)

    rows = await enterprise_api.list_org_members(
        department_id=None,
        search=None,
        tenant_id=None,
        current_user=make_user(),
        db=db,
    )

    assert rows == [
        {
            "id": str(wecom_member.id),
            "provider": "wecom",
            "name": "张三",
            "email": "zhangsan@example.com",
            "title": "Engineer",
            "department_path": "Root / Engineering",
            "avatar_url": None,
        }
    ]
    assert "sync_provider" in str(db.executed[0])


@pytest.mark.asyncio
async def test_deliver_broadcast_emails_continues_after_single_failure(monkeypatch):
    from app.services.system_email_service import BroadcastEmailRecipient, deliver_broadcast_emails

    delivered = []

    async def fake_send_system_email(email: str, subject: str, body: str) -> None:
        if email == "bad@example.com":
            raise RuntimeError("smtp down")
        delivered.append((email, subject, body))

    monkeypatch.setattr("app.services.system_email_service.send_system_email", fake_send_system_email)

    await deliver_broadcast_emails([
        BroadcastEmailRecipient(email="bad@example.com", subject="s1", body="b1"),
        BroadcastEmailRecipient(email="good@example.com", subject="s2", body="b2"),
    ])

    assert delivered == [("good@example.com", "s2", "b2")]
