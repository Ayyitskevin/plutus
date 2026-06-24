"""Public client order tracking — magic link, no login."""
from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app import config, db, order_tracking, orders, tenants


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
    monkeypatch.setattr(config, "ALLOW_SIMULATE_PAYMENT", True)
    monkeypatch.setattr(config, "STRIPE_SECRET_KEY", "sk_test_pytest")
    monkeypatch.setattr(config, "SAAS_PUBLIC_URL", "http://plutus.test")
    db.migrate()
    from app.main import app

    return TestClient(app)


def _tiny_jpeg() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (40, 30), color=(100, 150, 200)).save(buf, format="JPEG")
    return buf.getvalue()


def _paid_order() -> tuple[int, str]:
    tenants.create_tenant("trackco", name="Track Studio", store_slug="track-co")
    from app import service

    folder = config.DATA_DIR / "g"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "a.jpg").write_bytes(_tiny_jpeg())
    run_id = service.analyze_folder(folder, name="Demo", tenant_id="trackco")["run_id"]
    prepared = orders.prepare_bundle_order(
        tenant_id="trackco",
        run_id=run_id,
        bundle_index=0,
        client_email="client@example.com",
        client_name="Alex Client",
    )
    order_id = prepared["order_id"]
    pay = orders.simulate_test_payment(order_id)
    token = pay["client_track_url"].rsplit("/", 1)[-1]
    return order_id, token


def test_client_track_page_public(saas_client):
    order_id, token = _paid_order()

    r = saas_client.get(f"/store/order/track/{token}")
    assert r.status_code == 200
    assert b"Track Studio" in r.content
    assert f"Order #{order_id}".encode() in r.content
    assert b"paid" in r.content.lower()
    assert b"submitted" in r.content.lower()


def test_client_track_unknown_token(saas_client):
    r = saas_client.get("/store/order/track/not-a-real-token-xyz")
    assert r.status_code == 404


def test_client_track_no_auth_required(saas_client):
    _, token = _paid_order()
    saas_client.cookies.clear()
    r = saas_client.get(f"/store/order/track/{token}")
    assert r.status_code == 200


def test_order_created_with_client_token(saas_client):
    tenants.create_tenant("tokco", name="Tok", store_slug="tok-co")
    from app import service

    folder = config.DATA_DIR / "tok"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "a.jpg").write_bytes(_tiny_jpeg())
    run_id = service.analyze_folder(folder, name="Tok", tenant_id="tokco")["run_id"]
    prepared = orders.prepare_bundle_order(
        tenant_id="tokco",
        run_id=run_id,
        bundle_index=0,
    )
    order = db.get_order(prepared["order_id"])
    assert order is not None
    assert order.get("client_token")


def test_mark_order_paid_returns_track_url(saas_client):
    order_id, token = _paid_order()
    url = order_tracking.client_track_url(token)
    assert url == f"http://plutus.test/store/order/track/{token}"
    order = db.get_order(order_id)
    assert order["client_token"] == token