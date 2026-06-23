"""Phase 6 — client purchase loop: share link → checkout → pay → lab → notify."""
from __future__ import annotations

import io
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app import config, db, lab, notifications, orders, tenants


@pytest.fixture()
def saas_client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(config, "SAAS_MODE", True)
    monkeypatch.setattr(config, "API_TOKEN", "admin-secret")
    monkeypatch.setattr(config, "TENANT_KEY_PEPPER", "pepper-secret")
    monkeypatch.setattr(config, "RATE_LIMIT_ENABLED", False)
    monkeypatch.setattr(config, "LAB_ADAPTER", "mock")
    monkeypatch.setattr(config, "LAB_MOCK_PROCESS_SECONDS", 0)
    monkeypatch.setattr(config, "LAB_MOCK_SHIP_SECONDS", 0)
    monkeypatch.setattr(config, "STRIPE_SECRET_KEY", "sk_test_phase6")
    monkeypatch.setattr(config, "ALLOW_SIMULATE_PAYMENT", True)
    monkeypatch.setattr(config, "SAAS_PUBLIC_URL", "http://plutus.test")
    db.migrate()
    from app.main import app

    return TestClient(app)


def _tiny_jpeg() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (40, 30), color=(100, 150, 200)).save(buf, format="JPEG")
    return buf.getvalue()


def _tenant_run(tenant_id: str = "shop") -> tuple[str, int]:
    tenants.create_tenant(tenant_id, name="Shop Studio", store_slug=tenant_id)
    issued = tenants.issue_api_key(tenant_id)
    from app import service

    folder = config.DATA_DIR / "g"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "a.jpg").write_bytes(_tiny_jpeg())
    result = service.analyze_folder(folder, name="Demo", tenant_id=tenant_id)
    return issued["api_key"], result["run_id"]


def test_share_link_api_returns_public_url(saas_client):
    api_key, run_id = _tenant_run("shop1")
    r = saas_client.post(
        "/storefront/share-links",
        headers={"Authorization": f"Bearer {api_key}"},
        data={"run_id": run_id, "label": "Wedding offer"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["public_url"] == f"http://plutus.test/store/shop1/offer/{body['token']}"
    assert body["store_slug"] == "shop1"


def test_store_offer_and_checkout_session(saas_client):
    api_key, run_id = _tenant_run("shop2")
    from app.storefront import create_share_link

    link = create_share_link(tenant_id="shop2", run_id=run_id)
    r = saas_client.get(link["url"])
    assert r.status_code == 200
    assert b"Buy this package" in r.content

    fake_session = {
        "id": "cs_test_123",
        "url": "https://checkout.stripe.test/session",
    }
    with patch("app.billing._stripe_request", return_value=fake_session):
        r = saas_client.post(
            link["url"] + "/checkout",
            data={
                "bundle_index": 0,
                "client_email": "buyer@example.com",
                "client_name": "Buyer",
            },
            follow_redirects=False,
        )
    assert r.status_code == 303
    assert r.headers["location"] == fake_session["url"]

    pending = db.list_orders(tenant_id="shop2", limit=5)
    assert len(pending) == 1
    assert pending[0]["status"] == "pending"
    assert pending[0]["client_email"] == "buyer@example.com"


def test_stripe_webhook_marks_paid_and_submits_lab(saas_client, monkeypatch):
    monkeypatch.setattr(config, "ORDER_WEBHOOK_URL", "https://hooks.test/plutus")
    api_key, run_id = _tenant_run("shop3")
    prepared = orders.prepare_bundle_order(
        tenant_id="shop3",
        run_id=run_id,
        bundle_index=0,
        client_email="client@example.com",
    )
    order_id = prepared["order_id"]
    db.update_order(order_id, stripe_session_id="cs_webhook_test")

    event = {
        "id": "evt_test_phase6",
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "id": "cs_webhook_test",
                "metadata": {
                    "order_id": str(order_id),
                    "tenant_id": "shop3",
                    "checkout_kind": "client_bundle",
                },
                "customer_details": {"email": "paid@example.com"},
                "payment_intent": "pi_test_1",
            }
        },
    }
    import json

    with patch("app.billing.verify_webhook_signature", return_value=True):
        with patch("httpx.Client") as client_cls:
            client = client_cls.return_value.__enter__.return_value
            client.post.return_value.status_code = 200
            r = saas_client.post(
                "/webhooks/stripe",
                content=json.dumps(event).encode(),
                headers={"stripe-signature": "t=1,v1=x"},
            )
    assert r.status_code == 200

    order = db.get_order(order_id)
    assert order is not None
    assert order["status"] == "paid"
    assert order["lab_status"] == "submitted"
    assert order["lab_ref"]

    events = db.list_fulfillment_events(order_id)
    assert any(e["status"] == "submitted" for e in events)


def test_simulate_payment_api(saas_client):
    api_key, run_id = _tenant_run("shop4")
    prepared = orders.prepare_bundle_order(
        tenant_id="shop4",
        run_id=run_id,
        bundle_index=0,
        client_email="sim@example.com",
    )
    order_id = prepared["order_id"]

    r = saas_client.post(
        f"/orders/{order_id}/simulate-payment",
        headers={"Authorization": f"Bearer {api_key}"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "paid"
    assert body["lab_status"] == "submitted"

    order = db.get_order(order_id)
    assert order is not None
    assert order["status"] == "paid"


def test_mark_order_paid_idempotent(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(config, "LAB_ADAPTER", "mock")
    db.migrate()
    tenants.create_tenant("t1", name="T1", store_slug="t1")
    gid = db.insert_gallery(name="g", source="/x", photo_count=1, tenant_id="t1")
    rid = db.insert_run(
        gallery_id=gid,
        engine="mock",
        bundle_count=1,
        estimated_total_cents=1000,
        payload={"bundles": []},
        tenant_id="t1",
    )
    oid = db.create_order(
        tenant_id="t1",
        run_id=rid,
        bundle_index=0,
        total_cents=4500,
        items=[{"sku": "print-8x10", "label": "Print", "quantity": 1, "unit_cents": 4500}],
    )
    db.update_order(oid, status="paid", lab_status="submitted", lab_ref="mock-1")

    with patch.object(notifications, "notify_order_paid") as notify:
        with patch.object(lab, "submit_order") as submit:
            result = orders.mark_order_paid(oid)
    assert result.get("already_paid") is True
    submit.assert_not_called()
    notify.assert_not_called()