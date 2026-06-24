"""Photographer order ops — detail enrichment, poll, resend."""
from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app import config, db, notifications, orders, tenants


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
    monkeypatch.setattr(config, "SAAS_PUBLIC_URL", "http://plutus.test")
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
    db.migrate()
    tenants.create_tenant("ops", name="Ops Studio", store_slug="ops")
    gid = db.insert_gallery(name="G", source="/x", photo_count=1, tenant_id="ops")
    rid = db.insert_run(
        gallery_id=gid,
        engine="mock",
        bundle_count=1,
        estimated_total_cents=4500,
        payload={
            "bundles": [
                {
                    "title": "Album Set",
                    "pitch": "Best picks",
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
        tenant_id="ops",
    )
    prepared = orders.prepare_bundle_order(
        tenant_id="ops",
        run_id=rid,
        bundle_index=0,
        client_email="buyer@example.com",
        client_name="Buyer",
    )
    oid = prepared["order_id"]
    orders.mark_order_paid(oid, client_email="buyer@example.com")
    return oid, rid


def test_order_detail_shows_bundle_title(saas_client, paid_order):
    oid, _rid = paid_order
    db.update_tenant("ops", email_verified_at="2026-01-01T00:00:00+00:00")
    api_key = tenants.issue_api_key("ops")["api_key"]
    _login(saas_client, api_key)

    page = saas_client.get(f"/ui/saas/app/orders/{oid}")
    assert page.status_code == 200
    assert b"Album Set" in page.content
    assert b"Copy link" in page.content
    assert b"/store/order/track/" in page.content


def test_poll_lab_redirects(saas_client, paid_order):
    oid, _rid = paid_order
    db.update_tenant("ops", email_verified_at="2026-01-01T00:00:00+00:00")
    api_key = tenants.issue_api_key("ops")["api_key"]
    _login(saas_client, api_key)

    r = saas_client.post(
        f"/ui/saas/app/orders/{oid}/poll-lab",
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert f"orders/{oid}?lab_polled=1" in r.headers["location"]


def test_resend_confirmation(saas_client, paid_order):
    oid, _rid = paid_order
    db.update_tenant("ops", email_verified_at="2026-01-01T00:00:00+00:00")
    api_key = tenants.issue_api_key("ops")["api_key"]
    _login(saas_client, api_key)

    with patch("app.notifications.resend_client_confirmation", return_value=True) as resend:
        r = saas_client.post(
            f"/ui/saas/app/orders/{oid}/resend-confirmation",
            follow_redirects=False,
        )
    assert r.status_code == 303
    assert f"orders/{oid}?resent=1" in r.headers["location"]
    resend.assert_called_once_with(oid)


def test_resend_client_confirmation_requires_paid(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(config, "SMTP_HOST", "smtp.test")
    monkeypatch.setattr(config, "SMTP_FROM", "plutus@test")
    db.migrate()
    tenants.create_tenant("x", name="X", store_slug="x")
    gid = db.insert_gallery(name="g", source="/x", photo_count=1, tenant_id="x")
    rid = db.insert_run(
        gallery_id=gid,
        engine="mock",
        bundle_count=1,
        estimated_total_cents=100,
        payload={"bundles": []},
        tenant_id="x",
    )
    oid = db.create_order(
        tenant_id="x",
        run_id=rid,
        bundle_index=0,
        total_cents=100,
        items=[],
        client_email="a@test.com",
    )
    assert notifications.resend_client_confirmation(oid) is False