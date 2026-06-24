"""Signup email verification flow."""
from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app import config, db, signup, signup_verify


@pytest.fixture()
def saas_client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(config, "SAAS_MODE", True)
    monkeypatch.setattr(config, "SIGNUP_ENABLED", True)
    monkeypatch.setattr(config, "SIGNUP_VERIFY_EMAIL", True)
    monkeypatch.setattr(config, "SIGNUP_VERIFY_DEV_BYPASS", False)
    monkeypatch.setattr(config, "SMTP_HOST", "smtp.test")
    monkeypatch.setattr(config, "SMTP_FROM", "verify@plutus.test")
    monkeypatch.setattr(config, "API_TOKEN", "admin-secret")
    monkeypatch.setattr(config, "TENANT_KEY_PEPPER", "pepper-secret")
    monkeypatch.setattr(config, "RATE_LIMIT_ENABLED", False)
    monkeypatch.setattr(config, "SAAS_PUBLIC_URL", "http://plutus.test")
    db.migrate()
    from app.main import app

    return TestClient(app)


def test_signup_requires_verification_before_api_key_works(saas_client):
    with patch("app.notifications._send_email", return_value=True):
        result = signup.register_studio(
            studio_name="Verify Studio",
            email="owner@verify.test",
            store_slug="verify-studio",
        )
    assert result["verification_required"] is True
    assert result["api_key"] is None

    row = db.get_pending_signup_verification_by_email("owner@verify.test")
    assert row is not None
    assert not row.get("key_id")

    verify = saas_client.get(f"/ui/saas/verify-email?token={row['token']}")
    assert verify.status_code == 200
    assert b"plutus_tk_verify-studio_" in verify.content

    tenant = db.get_tenant("verify-studio")
    assert tenant and tenant.get("email_verified_at")
    from app import tenants

    match = re.search(r"plutus_tk_verify-studio_[a-f0-9]+", verify.content.decode())
    assert match
    assert tenants.resolve_api_key(match.group(0)) is not None


def test_verify_token_marks_tenant_verified_and_issues_key(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(config, "SAAS_MODE", True)
    monkeypatch.setattr(config, "SIGNUP_VERIFY_EMAIL", True)
    monkeypatch.setattr(config, "SMTP_HOST", "smtp.test")
    monkeypatch.setattr(config, "SMTP_FROM", "verify@plutus.test")
    db.migrate()

    with patch("app.notifications._send_email", return_value=True):
        result = signup.register_studio(
            studio_name="Tok",
            email="tok@verify.test",
            store_slug="tok-verify",
        )
    assert result["verification_required"] is True
    pending = db.get_pending_signup_verification_by_email("tok@verify.test")
    assert pending
    verified = signup_verify.verify_token(pending["token"])
    assert verified["api_key"].startswith("plutus_tk_tok-verify_")
    tenant = db.get_tenant("tok-verify")
    assert tenant and tenant.get("email_verified_at")


def test_signup_skips_verify_without_smtp(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(config, "SAAS_MODE", True)
    monkeypatch.setattr(config, "SIGNUP_ENABLED", True)
    monkeypatch.setattr(config, "SIGNUP_VERIFY_EMAIL", True)
    monkeypatch.setattr(config, "SMTP_HOST", None)
    db.migrate()

    result = signup.register_studio(
        studio_name="No SMTP",
        email="nosmtp@test.com",
        store_slug="no-smtp",
    )
    assert result["verification_required"] is False
    assert result["api_key"]
    tenant = db.get_tenant("no-smtp")
    assert tenant and tenant.get("email_verified_at")


def test_resend_verification_sends_email(saas_client):
    with patch("app.notifications._send_email", return_value=True) as send:
        signup.register_studio(
            studio_name="Resend Studio",
            email="resend@verify.test",
            store_slug="resend-studio",
        )
        send.reset_mock()
        r = saas_client.post(
            "/ui/saas/resend-verification",
            data={"email": "resend@verify.test", "return_to": "pending"},
            follow_redirects=False,
        )
    assert r.status_code == 303
    assert "resent=1" in r.headers["location"]
    send.assert_called_once()


def test_verify_token_rejects_expired_and_malformed_expiry(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(config, "SAAS_MODE", True)
    monkeypatch.setattr(config, "SIGNUP_VERIFY_EMAIL", True)
    monkeypatch.setattr(config, "SMTP_HOST", "smtp.test")
    db.migrate()

    db.insert_signup_verification(
        token="expired-token",
        tenant_id="exp-test",
        email="exp@test.com",
        key_id=None,
        expires_at=(datetime.now(UTC) - timedelta(hours=1)).isoformat(),
    )
    from app import tenants

    tenants.create_tenant("exp-test", name="Exp", store_slug="exp-test")
    with pytest.raises(signup_verify.SignupVerifyError, match="expired"):
        signup_verify.verify_token("expired-token")

    db.insert_signup_verification(
        token="bad-expiry",
        tenant_id="exp-test",
        email="bad@test.com",
        key_id=None,
        expires_at="not-a-timestamp",
    )
    with pytest.raises(signup_verify.SignupVerifyError, match="invalid"):
        signup_verify.verify_token("bad-expiry")


def test_admin_create_tenant_works_with_email_verify_on(saas_client):
    from app import tenants

    r = saas_client.post(
        "/ui/saas/login",
        data={"api_token": "admin-secret"},
        follow_redirects=False,
    )
    assert r.status_code == 303

    r = saas_client.post(
        "/ui/saas/app/admin/tenants",
        data={
            "tenant_id": "admin-verified",
            "name": "Admin Verified Studio",
            "store_slug": "admin-verified",
        },
        follow_redirects=False,
    )
    assert r.status_code == 200
    assert b"plutus_tk_admin-verified_" in r.content

    tenant = db.get_tenant("admin-verified")
    assert tenant and tenant.get("email_verified_at")

    match = re.search(r"plutus_tk_admin-verified_[a-f0-9]+", r.content.decode())
    assert match
    assert tenants.resolve_api_key(match.group(0)) is not None