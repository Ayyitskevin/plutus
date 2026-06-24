"""Money path and webhook idempotency hardening."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from app import billing, config, db, lab, notifications, orders, tenants


@pytest.fixture()
def order_env(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(config, "LAB_ADAPTER", "mock")
    db.migrate()
    tenants.create_tenant("payco", name="Pay Co", store_slug="pay-co")
    gid = db.insert_gallery(name="g", source="/x", photo_count=1, tenant_id="payco")
    rid = db.insert_run(
        gallery_id=gid,
        engine="mock",
        bundle_count=1,
        estimated_total_cents=1000,
        payload={"bundles": []},
        tenant_id="payco",
    )
    oid = db.create_order(
        tenant_id="payco",
        run_id=rid,
        bundle_index=0,
        total_cents=4500,
        items=[{"sku": "print-8x10", "label": "Print", "quantity": 1, "unit_cents": 4500}],
    )
    return oid


def test_mark_order_paid_increments_usage_once(order_env):
    oid = order_env
    with patch.object(lab, "submit_order", return_value={"lab_status": "submitted"}):
        with patch.object(notifications, "notify_order_paid", return_value={}):
            orders.mark_order_paid(oid, stripe_payment_intent="pi_1")
            usage_after_first = db.get_tenant_usage("payco")["orders"]
            orders.mark_order_paid(oid, stripe_payment_intent="pi_2")
            usage_after_second = db.get_tenant_usage("payco")["orders"]
    assert usage_after_first == 1
    assert usage_after_second == 1


def test_checkout_webhook_event_recorded_for_dedup(tmp_path, monkeypatch, order_env):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(config, "STRIPE_WEBHOOK_SECRET", "whsec_test")
    oid = order_env
    db.update_order(oid, stripe_session_id="cs_dedup_1")

    event = {
        "id": "evt_checkout_dedup",
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "id": "cs_dedup_1",
                "payment_intent": "pi_dedup",
                "metadata": {"order_id": str(oid), "checkout_kind": "client_bundle"},
            }
        },
    }
    with patch.object(lab, "submit_order", return_value={"lab_status": "submitted"}):
        with patch.object(notifications, "notify_order_paid", return_value={}):
            billing.handle_webhook_event(event)
    assert db.is_stripe_webhook_processed("evt_checkout_dedup")

    calls = {"n": 0}
    original = orders.mark_order_paid

    def counting_mark(*args, **kwargs):
        calls["n"] += 1
        return original(*args, **kwargs)

    with patch.object(orders, "mark_order_paid", side_effect=counting_mark):
        billing.handle_webhook_event(event)
    assert calls["n"] == 0


def test_revoked_key_invalidates_ui_session(saas_client):
    from app import tenants

    tenants.create_tenant("rev", name="Rev Co", store_slug="rev")
    issued = tenants.issue_api_key("rev")
    db.update_tenant("rev", email_verified_at="2026-01-01T00:00:00+00:00")
    saas_client.post(
        "/ui/saas/login",
        data={"api_token": issued["api_key"]},
        follow_redirects=False,
    )
    tenants.revoke_key(issued["key_id"])
    r = saas_client.get("/ui/saas/app", follow_redirects=False)
    assert r.status_code == 303
    assert "/ui/saas/login" in r.headers["location"]


@pytest.fixture()
def saas_client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(config, "SAAS_MODE", True)
    monkeypatch.setattr(config, "API_TOKEN", "admin-secret")
    monkeypatch.setattr(config, "TENANT_KEY_PEPPER", "pepper-secret")
    monkeypatch.setattr(config, "RATE_LIMIT_ENABLED", False)
    db.migrate()
    from fastapi.testclient import TestClient

    from app.main import app

    return TestClient(app)