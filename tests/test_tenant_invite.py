"""M6 — admin invite tokens (one-time claim URL)."""
from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app import config, db, tenants


@pytest.fixture()
def saas_client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(config, "SAAS_MODE", True)
    monkeypatch.setattr(config, "API_TOKEN", "admin-secret")
    monkeypatch.setattr(config, "TENANT_KEY_PEPPER", "pepper-secret")
    monkeypatch.setattr(config, "RATE_LIMIT_ENABLED", False)
    monkeypatch.setattr(config, "SAAS_PUBLIC_URL", "http://plutus.test")
    monkeypatch.setattr(config, "SMTP_HOST", "smtp.test")
    monkeypatch.setattr(config, "SMTP_FROM", "noreply@test")
    db.migrate()
    from app.main import app

    return TestClient(app)


def test_admin_create_sends_invite_link_not_api_key(saas_client):
    saas_client.post(
        "/ui/saas/login",
        data={"api_token": "admin-secret"},
        follow_redirects=False,
    )
    with patch("app.notifications.send_tenant_welcome_email", return_value=True) as send:
        r = saas_client.post(
            "/ui/saas/app/admin/tenants",
            data={
                "tenant_id": "inv",
                "name": "Invite Studio",
                "store_slug": "invite-studio",
                "notify_email": "photo@invite.test",
            },
            follow_redirects=False,
        )
    assert r.status_code == 200
    assert b"Welcome email sent" in r.content
    assert b"plutus_tk_" not in r.content
    send.assert_called_once()
    assert send.call_args.kwargs["invite_url"].startswith(
        "http://plutus.test/ui/saas/claim-invite?token="
    )
    assert send.call_args.kwargs.get("api_key") is None
    assert db.list_tenant_keys("inv") == []


def test_claim_invite_issues_key_and_logs_in(saas_client):
    from app import tenant_invite

    tenants.create_tenant("claim", name="Claim Studio", store_slug="claim")
    db.update_tenant("claim", notify_email="ops@claim.test")
    token = tenant_invite.create_invite(tenant_id="claim", email="ops@claim.test")

    r = saas_client.get(f"/ui/saas/claim-invite?token={token}", follow_redirects=False)
    assert r.status_code == 200
    assert b"plutus_tk_" in r.content
    assert "plutus_sid" in r.cookies

    keys = db.list_tenant_keys("claim")
    assert len(keys) == 1
    assert keys[0]["label"] == "invite"

    r2 = saas_client.get(f"/ui/saas/claim-invite?token={token}")
    assert b"already claimed" in r2.content


def test_resend_welcome_rotates_invite_token(saas_client):
    from app import tenant_invite

    tenants.create_tenant("res", name="Resend", store_slug="res")
    db.update_tenant("res", notify_email="ops@res.test")
    saas_client.post(
        "/ui/saas/login",
        data={"api_token": "admin-secret"},
        follow_redirects=False,
    )
    token1 = tenant_invite.create_invite(tenant_id="res", email="ops@res.test")
    with patch("app.notifications.send_tenant_welcome_email", return_value=True) as send:
        r = saas_client.post(
            "/ui/saas/app/admin/tenants/res/resend-welcome",
            follow_redirects=False,
        )
    assert r.status_code == 200
    invite_url = send.call_args.kwargs["invite_url"]
    token2 = invite_url.rsplit("token=", 1)[-1]
    assert token2 != token1
    assert db.get_tenant_invite(token1) is None