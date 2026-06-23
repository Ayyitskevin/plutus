"""Lab fulfillment notifications — shipped / complete."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from app import config, db, lab, notifications, tenants


@pytest.fixture()
def lab_db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(config, "LAB_ADAPTER", "mock")
    monkeypatch.setattr(config, "LAB_MOCK_PROCESS_SECONDS", 0)
    monkeypatch.setattr(config, "LAB_MOCK_SHIP_SECONDS", 0)
    monkeypatch.setattr(config, "NOTIFY_LAB_SHIPPED", True)
    monkeypatch.setattr(config, "ORDER_WEBHOOK_URL", "https://hooks.test/plutus")
    db.migrate()
    tenants.create_tenant("homelab", name="Studio", store_slug="studio")
    gid = db.insert_gallery(name="g", source="/x", photo_count=1, tenant_id="homelab")
    rid = db.insert_run(
        gallery_id=gid,
        engine="mock",
        bundle_count=1,
        estimated_total_cents=1000,
        payload={"bundles": []},
        tenant_id="homelab",
    )
    oid = db.create_order(
        tenant_id="homelab",
        run_id=rid,
        bundle_index=0,
        total_cents=4500,
        items=[{"sku": "print-8x10", "label": "Print", "quantity": 1, "unit_cents": 4500}],
        client_email="client@example.com",
        client_name="Client",
    )
    db.update_order(oid, status="paid", lab_ref="mock-1", lab_status="submitted")
    return oid


def test_notify_lab_shipped_webhook(lab_db):
    with patch("httpx.Client") as client_cls:
        client = client_cls.return_value.__enter__.return_value
        client.post.return_value.status_code = 200
        out = notifications.notify_lab_status(lab_db, "shipped")
    assert out["webhook"] is True
    assert client.post.call_args.kwargs["json"]["event"] == "order.lab.shipped"


def test_poll_order_notifies_on_ship(lab_db):
    lab.poll_order(lab_db)  # submitted -> processing
    with patch("app.notifications.notify_lab_status") as notify:
        lab.poll_order(lab_db)  # processing -> shipped
    notify.assert_called_once_with(lab_db, "shipped")