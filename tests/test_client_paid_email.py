"""Client order confirmation email on paid."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from app import config, db, notifications, tenants


@pytest.fixture()
def notify_db(tmp_path, monkeypatch):
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
                    "title": "Favorites",
                    "items": [
                        {
                            "sku": "print-8x10",
                            "label": "Print",
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
        items=[{"sku": "print-8x10", "label": "Print", "quantity": 1, "unit_cents": 4500}],
        client_email="client@example.com",
        client_name="Client",
    )
    db.update_order(oid, status="paid", lab_ref="mock-1", lab_status="submitted")
    order = db.get_order(oid)
    return oid, order["client_token"]


def test_notify_order_paid_emails_client(notify_db):
    oid, token = notify_db
    with patch("app.notifications._send_email") as send:
        send.return_value = True
        out = notifications.notify_order_paid(oid)
    assert out["client_email"] is True
    client_call = [c for c in send.call_args_list if c.kwargs.get("to") == "client@example.com"]
    assert len(client_call) == 1
    body = client_call[0].kwargs["body"]
    assert token in body
    assert "plutus.test/store/order/track" in body
    assert "Favorites" in body
    assert "$45.00" in body