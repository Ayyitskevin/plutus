from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app import catalog, config, db, lab, notifications


@pytest.fixture()
def saas_client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(config, "SAAS_MODE", True)
    monkeypatch.setattr(config, "API_TOKEN", "admin-secret")
    monkeypatch.setattr(config, "TENANT_KEY_PEPPER", "pepper-secret")
    monkeypatch.setattr(config, "RATE_LIMIT_ENABLED", False)
    monkeypatch.setattr(config, "LAB_ADAPTER", "mock")
    db.migrate()
    from app.main import app

    return TestClient(app)


def test_tenant_catalog_pricing(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.db")
    from app import tenants

    db.migrate()
    tenants.create_tenant("studio", name="Studio", store_slug="studio")
    db.upsert_product_override("studio", "print-8x10", unit_cents=5000, label="Studio 8×10")

    rows = catalog.list_catalog("studio")
    row = next(r for r in rows if r["sku"] == "print-8x10")
    assert row["unit_cents"] == 5000
    assert row["label"] == "Studio 8×10"
    assert catalog.unit_cents_for("print-8x10", "studio") == 5000


def test_lab_submit_on_payment(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.db")
    db.migrate()

    from app import tenants

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
    db.update_order(oid, status="paid")

    result = lab.submit_order(oid)
    assert result["lab_status"] == "submitted"
    assert result["lab_ref"].startswith(f"mock-{oid}-")

    order = db.get_order(oid)
    assert order["lab_status"] == "submitted"
    assert order["lab_ref"]


def test_notify_order_webhook(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(config, "ORDER_WEBHOOK_URL", "https://hooks.test/plutus")
    db.migrate()

    from app import tenants

    tenants.create_tenant("t1", name="T1", store_slug="t1")
    db.update_tenant("t1", notify_email="photo@studio.com")
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
        client_email="client@example.com",
    )
    db.update_order(oid, status="paid", lab_ref="mock-1", lab_status="submitted")

    with patch("httpx.Client") as client_cls:
        client = client_cls.return_value.__enter__.return_value
        client.post.return_value.status_code = 200
        out = notifications.notify_order_paid(oid)
    assert out["webhook"] is True
    client.post.assert_called_once()


def test_admin_tenant_page(saas_client):
    saas_client.post("/ui/saas/login", data={"api_token": "admin-secret"}, follow_redirects=False)
    saas_client.post(
        "/ui/saas/app/admin/tenants",
        data={"tenant_id": "gamma", "name": "Gamma Co", "store_slug": "gamma"},
        follow_redirects=False,
    )
    r = saas_client.get("/ui/saas/app/admin/tenants/gamma")
    assert r.status_code == 200
    assert b"Gamma Co" in r.content
    assert b"Order notify email" in r.content