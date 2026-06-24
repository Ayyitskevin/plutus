"""SMTP notifications — richer client email and dashboard test send."""
from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app import config, db, notifications, tenants


@pytest.fixture()
def saas_client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(config, "SAAS_MODE", True)
    monkeypatch.setattr(config, "API_TOKEN", "admin-secret")
    monkeypatch.setattr(config, "TENANT_KEY_PEPPER", "pepper-secret")
    monkeypatch.setattr(config, "RATE_LIMIT_ENABLED", False)
    monkeypatch.setattr(config, "SMTP_HOST", "smtp.test")
    monkeypatch.setattr(config, "SMTP_FROM", "plutus@test")
    db.migrate()
    from app.main import app

    return TestClient(app)


def _login(client: TestClient, api_key: str) -> None:
    r = client.post(
        "/ui/saas/login",
        data={"api_token": api_key},
        follow_redirects=False,
    )
    assert r.status_code == 303


@pytest.fixture()
def paid_order(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(config, "SMTP_HOST", "smtp.test")
    monkeypatch.setattr(config, "SMTP_FROM", "plutus@test")
    monkeypatch.setattr(config, "NOTIFY_CLIENT_ON_PAID", True)
    monkeypatch.setattr(config, "SAAS_PUBLIC_URL", "http://plutus.test")
    db.migrate()
    tenants.create_tenant("studio", name="Studio", store_slug="studio")
    gid = db.insert_gallery(name="g", source="/x", photo_count=1, tenant_id="studio")
    rid = db.insert_run(
        gallery_id=gid,
        engine="mock",
        bundle_count=1,
        estimated_total_cents=4500,
        payload={
            "bundles": [
                {
                    "title": "Wedding Highlights",
                    "items": [
                        {
                            "sku": "print-8x10",
                            "label": "8×10 Print",
                            "unit_cents": 4500,
                            "quantity": 1,
                            "photo": "01.jpg",
                        }
                    ],
                }
            ]
        },
        tenant_id="studio",
    )
    oid = db.create_order(
        tenant_id="studio",
        run_id=rid,
        bundle_index=0,
        total_cents=4500,
        items=[{"sku": "print-8x10", "label": "8×10 Print", "quantity": 1, "unit_cents": 4500}],
        client_email="client@example.com",
        client_name="Client",
    )
    db.update_order(oid, status="paid", lab_ref="mock-1", lab_status="submitted")
    order = db.get_order(oid)
    return oid, order["client_token"]


def test_notify_order_paid_client_email_includes_bundle_and_total(paid_order):
    oid, token = paid_order
    with patch("app.notifications._send_email") as send:
        send.return_value = True
        out = notifications.notify_order_paid(oid)
    assert out["client_email"] is True
    client_call = [c for c in send.call_args_list if c.kwargs.get("to") == "client@example.com"]
    assert len(client_call) == 1
    body = client_call[0].kwargs["body"]
    assert "Wedding Highlights" in body
    assert "8×10 Print" in body
    assert "$45.00" in body
    assert token in body
    assert "plutus.test/store/order/track" in body


def test_dashboard_send_test_email(saas_client):
    tenants.create_tenant("mailco", name="Mail Co", store_slug="mail-co")
    db.update_tenant(
        "mailco",
        email_verified_at="2026-01-01T00:00:00+00:00",
        notify_email="ops@mailco.test",
    )
    api_key = tenants.issue_api_key("mailco")["api_key"]
    _login(saas_client, api_key)

    with patch("app.notifications.send_test_email", return_value=True) as send:
        r = saas_client.post(
            "/ui/saas/app/notifications/test",
            follow_redirects=False,
        )
    assert r.status_code == 303
    assert "notification_test_sent=1" in r.headers["location"]
    send.assert_called_once()
    assert send.call_args.kwargs["to"] == "ops@mailco.test"

    page = saas_client.get("/ui/saas/app?notification_test_sent=1")
    assert b"SMTP ready" in page.content
    assert b"Test email sent" in page.content


def test_admin_create_tenant_sends_welcome_email(saas_client):
    saas_client.post(
        "/ui/saas/login",
        data={"api_token": "admin-secret"},
        follow_redirects=False,
    )
    with patch("app.notifications.send_tenant_welcome_email", return_value=True) as send:
        r = saas_client.post(
            "/ui/saas/app/admin/tenants",
            data={
                "tenant_id": "invite",
                "name": "Invite Studio",
                "store_slug": "invite-studio",
                "notify_email": "photo@invite.test",
            },
            follow_redirects=False,
        )
    assert r.status_code == 200
    assert b"Welcome email sent" in r.content
    send.assert_called_once()
    assert send.call_args.kwargs["to"] == "photo@invite.test"
    tenant = db.get_tenant("invite")
    assert tenant and tenant["notify_email"] == "photo@invite.test"


def test_admin_resend_welcome_email(saas_client):
    saas_client.post(
        "/ui/saas/login",
        data={"api_token": "admin-secret"},
        follow_redirects=False,
    )
    saas_client.post(
        "/ui/saas/app/admin/tenants",
        data={
            "tenant_id": "resend",
            "name": "Resend Studio",
            "notify_email": "ops@resend.test",
        },
        follow_redirects=False,
    )
    with patch("app.notifications.send_tenant_welcome_email", return_value=True) as send:
        r = saas_client.post(
            "/ui/saas/app/admin/tenants/resend/resend-welcome",
            follow_redirects=False,
        )
    assert r.status_code == 200
    assert b"Welcome email resent" in r.content
    send.assert_called_once()
    assert send.call_args.kwargs["to"] == "ops@resend.test"


def test_admin_create_tenant_skips_welcome_without_smtp(saas_client, monkeypatch):
    monkeypatch.setattr(config, "SMTP_HOST", None)
    monkeypatch.setattr(config, "SMTP_FROM", None)
    saas_client.post(
        "/ui/saas/login",
        data={"api_token": "admin-secret"},
        follow_redirects=False,
    )
    with patch("app.notifications.send_tenant_welcome_email") as send:
        r = saas_client.post(
            "/ui/saas/app/admin/tenants",
            data={
                "tenant_id": "nosmtp",
                "name": "No SMTP",
                "notify_email": "photo@nosmtp.test",
            },
            follow_redirects=False,
        )
    assert r.status_code == 200
    assert b"SMTP not configured" in r.content
    send.assert_not_called()


def test_dashboard_test_requires_notify_email(saas_client):
    tenants.create_tenant("empty", name="Empty", store_slug="empty")
    db.update_tenant("empty", email_verified_at="2026-01-01T00:00:00+00:00")
    api_key = tenants.issue_api_key("empty")["api_key"]
    _login(saas_client, api_key)

    r = saas_client.post(
        "/ui/saas/app/notifications/test",
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert "notification_test_error" in r.headers["location"]