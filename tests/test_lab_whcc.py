"""WHCC lab adapter — configured API path and webhook auth."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import httpx

from app import config, db, lab, lab_whcc, tenants


def _paid_order(*, tenant_id: str = "whcc-api") -> int:
    tenants.create_tenant(tenant_id, name="WHCC Studio", store_slug=tenant_id)
    gid = db.insert_gallery(name="g", source="/x", photo_count=1, tenant_id=tenant_id)
    rid = db.insert_run(
        gallery_id=gid,
        engine="mock",
        bundle_count=1,
        estimated_total_cents=1000,
        payload={"bundles": []},
        tenant_id=tenant_id,
    )
    oid = db.create_order(
        tenant_id=tenant_id,
        run_id=rid,
        bundle_index=0,
        total_cents=4500,
        items=[{
            "sku": "print-8x10",
            "label": "8x10",
            "quantity": 1,
            "unit_cents": 4500,
            "image_url": "https://cdn.example/hero.jpg",
        }],
    )
    paid_at = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
    db.update_order(oid, status="paid", paid_at=paid_at)
    return oid


def test_whcc_api_submit_and_poll(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(config, "LAB_ADAPTER", "whcc")
    monkeypatch.setattr(config, "WHCC_API_URL", "https://api.whcc.test/v1")
    monkeypatch.setattr(config, "WHCC_API_KEY", "secret")
    monkeypatch.setattr(config, "WHCC_ACCOUNT_ID", "acct-1")
    monkeypatch.setattr(config, "WHCC_RETRY_ATTEMPTS", 1)
    db.migrate()

    calls: list[tuple[str, str]] = []

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def request(self, method, url, **kwargs):
            calls.append((method, url))
            response = MagicMock()
            response.status_code = 200
            if method == "POST":
                response.json.return_value = {"order_id": "whcc-live-42", "status": "received"}
            else:
                response.json.return_value = {"status": "in_production"}
            return response

    monkeypatch.setattr(httpx, "Client", FakeClient)

    oid = _paid_order()
    submitted = lab.submit_order(oid)
    assert submitted["lab_ref"] == "whcc-live-42"
    assert submitted["lab_status"] == "submitted"

    polled = lab.poll_order(oid)
    assert polled["advanced"] is True
    assert polled["lab_status"] == "processing"
    assert calls[0][0] == "POST"
    assert calls[1][0] == "GET"


def test_verify_webhook_token(monkeypatch):
    monkeypatch.setattr(config, "WHCC_WEBHOOK_SECRET", "whcc-secret")
    assert lab_whcc.verify_webhook_token("Bearer whcc-secret") is True
    assert lab_whcc.verify_webhook_token("wrong") is False


def test_whcc_status_unreachable(monkeypatch):
    monkeypatch.setattr(config, "WHCC_API_URL", "https://api.whcc.test/v1")
    monkeypatch.setattr(config, "WHCC_API_KEY", "secret")
    monkeypatch.setattr(config, "WHCC_RETRY_ATTEMPTS", 1)

    def boom(*args, **kwargs):
        raise httpx.ConnectError("down", request=MagicMock())

    monkeypatch.setattr(httpx, "Client", boom)
    st = lab_whcc.whcc_status()
    assert st["configured"] is True
    assert st["reachable"] is False