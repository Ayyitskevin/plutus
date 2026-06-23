"""Phase 7 — async upload analyze, vision recommend, Stripe connectivity."""
from __future__ import annotations

import io
import json
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app import config, db, recommend, service, tenants, upload_worker


@pytest.fixture()
def saas_client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(config, "SAAS_MODE", True)
    monkeypatch.setattr(config, "API_TOKEN", "admin-secret")
    monkeypatch.setattr(config, "TENANT_KEY_PEPPER", "pepper-secret")
    monkeypatch.setattr(config, "RATE_LIMIT_ENABLED", False)
    monkeypatch.setattr(config, "UPLOAD_ASYNC_ANALYZE", True)
    monkeypatch.setattr(config, "ARGUS_AUTO_VISION", False)
    db.migrate()
    from app.main import app

    return TestClient(app)


def _tiny_jpeg() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (40, 30), color=(100, 150, 200)).save(buf, format="JPEG")
    return buf.getvalue()


def test_async_enqueue_returns_202(saas_client, tmp_path):
    tenants.create_tenant("async", name="Async", store_slug="async")
    issued = tenants.issue_api_key("async")
    from app import uploads

    batch = uploads.create_batch(tenant_id="async", name="Q")
    uploads.add_files(
        tenant_id="async",
        batch_id=batch["id"],
        files=[("a.jpg", _tiny_jpeg())],
    )
    r = saas_client.post(
        "/recommend/upload-batch",
        headers={"Authorization": f"Bearer {issued['api_key']}"},
        data={"batch_id": batch["id"]},
    )
    assert r.status_code == 202
    assert r.json()["status"] == "queued"

    updated = db.get_upload_batch(batch["id"], tenant_id="async")
    assert updated is not None
    assert updated["status"] == "queued"


def test_upload_worker_processes_batch(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(config, "ARGUS_AUTO_VISION", False)
    db.migrate()
    tenants.create_tenant("w", name="W", store_slug="w")
    from app import uploads

    batch = uploads.create_batch(tenant_id="w", name="Worker")
    batch_id = batch["id"]
    uploads.add_files(tenant_id="w", batch_id=batch_id, files=[("a.jpg", _tiny_jpeg())])
    service.enqueue_upload_batch_analyze(batch_id, tenant_id="w")
    assert upload_worker.process_pending_batches() == 1
    done = db.get_upload_batch(batch_id, tenant_id="w")
    assert done is not None
    assert done["status"] == "analyzed"
    assert done["run_id"]


def test_upload_batch_status_api(saas_client):
    tenants.create_tenant("st", name="ST", store_slug="st")
    issued = tenants.issue_api_key("st")
    from app import uploads

    batch = uploads.create_batch(tenant_id="st", name="Status")
    uploads.add_files(tenant_id="st", batch_id=batch["id"], files=[("a.jpg", _tiny_jpeg())])
    service.enqueue_upload_batch_analyze(batch["id"], tenant_id="st")
    r = saas_client.get(
        f"/upload-batches/{batch['id']}/status",
        headers={"Authorization": f"Bearer {issued['api_key']}"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["pending"] is True


def test_vision_recommend_food_theme():
    photos = [
        {
            "filename": "hero.jpg",
            "path": "/x/hero.jpg",
            "width": 4000,
            "height": 3000,
            "orientation": "landscape",
            "keeper_score": 0.91,
            "hero_potential": 0.93,
            "shot_type": "hero_plate",
            "keywords": ["food", "appetizer", "plating"],
        },
        {
            "filename": "detail.jpg",
            "path": "/x/detail.jpg",
            "width": 3000,
            "height": 3000,
            "orientation": "square",
            "keeper_score": 0.82,
            "hero_potential": 0.4,
            "shot_type": "detail",
            "keywords": ["macro", "ingredient"],
        },
    ]
    result = recommend.recommend_bundles(photos)
    assert result["engine"] == "vision"
    assert result["gallery_theme"] == "food"
    ids = {b["id"] for b in result["bundles"]}
    assert "metal-accent" in ids
    assert "gift-trio" in ids


def test_stripe_webhook_signed_payload(saas_client, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(config, "STRIPE_WEBHOOK_SECRET", "whsec_test_secret")
    monkeypatch.setattr(config, "LAB_ADAPTER", "mock")
    db.migrate()
    tenants.create_tenant("sw", name="SW", store_slug="sw")
    gid = db.insert_gallery(name="g", source="/x", photo_count=1, tenant_id="sw")
    rid = db.insert_run(
        gallery_id=gid,
        engine="mock",
        bundle_count=1,
        estimated_total_cents=1000,
        payload={"bundles": []},
        tenant_id="sw",
    )
    oid = db.create_order(
        tenant_id="sw",
        run_id=rid,
        bundle_index=0,
        total_cents=18500,
        items=[{"sku": "canvas-16x20", "label": "Canvas", "quantity": 1, "unit_cents": 18500}],
    )
    db.update_order(oid, stripe_session_id="cs_signed_test")

    from app import billing

    event = {
        "id": "evt_signed_test",
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "id": "cs_signed_test",
                "metadata": {
                    "order_id": str(oid),
                    "tenant_id": "sw",
                    "checkout_kind": "client_bundle",
                },
                "customer_details": {"email": "stripe@client.test"},
                "payment_intent": "pi_signed",
            }
        },
    }
    payload = json.dumps(event).encode()
    sig = billing.sign_webhook_payload(payload)
    r = saas_client.post(
        "/webhooks/stripe",
        content=payload,
        headers={"stripe-signature": sig},
    )
    assert r.status_code == 200
    order = db.get_order(oid)
    assert order is not None
    assert order["status"] == "paid"


def test_stripe_connectivity_mock(saas_client, monkeypatch):
    monkeypatch.setattr(config, "STRIPE_SECRET_KEY", "sk_test_phase7")
    with patch("app.billing._stripe_request", return_value={"available": []}):
        from app import billing

        st = billing.stripe_connectivity()
    assert st["configured"] is True
    assert st["reachable"] is True
    assert st["test_mode"] is True