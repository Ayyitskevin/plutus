"""Signup email verification flow."""
from __future__ import annotations

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
        r = saas_client.post(
            "/ui/saas/signup",
            data={
                "studio_name": "Verify Studio",
                "email": "owner@verify.test",
                "store_slug": "verify-studio",
            },
        )
    assert r.status_code == 200
    assert b"Check your email" in r.content
    assert b"plutus_tk_" not in r.content

    row = db.get_pending_signup_verification_by_email("owner@verify.test")
    assert row is not None
    api_key = row["api_key"]

    denied = saas_client.get(
        "/runs/1/json",
        headers={"Authorization": f"Bearer {api_key}"},
    )
    assert denied.status_code == 401

    verify = saas_client.get(f"/ui/saas/verify-email?token={row['token']}")
    assert verify.status_code == 200
    assert b"plutus_tk_verify-studio_" in verify.content

    tenant = db.get_tenant("verify-studio")
    assert tenant and tenant.get("email_verified_at")
    from app import tenants

    assert tenants.resolve_api_key(api_key) is not None


def test_verify_token_marks_tenant_verified(tmp_path, monkeypatch):
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
    assert verified["api_key"] == pending["api_key"]
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
    tenant = db.get_tenant("no-smtp")
    assert tenant and tenant.get("email_verified_at")