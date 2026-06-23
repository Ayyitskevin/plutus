"""Homelab storefront — share link → simulate pay → lab without full SaaS."""
from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app import config, db, homelab, orders


@pytest.fixture()
def homelab_client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(config, "SAAS_MODE", False)
    monkeypatch.setattr(config, "HOMELAB_STORE_ENABLED", True)
    monkeypatch.setattr(config, "HOMELAB_TENANT_ID", "homelab")
    monkeypatch.setattr(config, "HOMELAB_STORE_SLUG", "studio")
    monkeypatch.setattr(config, "API_TOKEN", "admin-secret")
    monkeypatch.setattr(config, "RATE_LIMIT_ENABLED", False)
    monkeypatch.setattr(config, "LAB_ADAPTER", "mock")
    monkeypatch.setattr(config, "LAB_MOCK_PROCESS_SECONDS", 0)
    monkeypatch.setattr(config, "LAB_MOCK_SHIP_SECONDS", 0)
    monkeypatch.setattr(config, "STRIPE_SECRET_KEY", "sk_test_homelab")
    monkeypatch.setattr(config, "ALLOW_SIMULATE_PAYMENT", True)
    monkeypatch.setattr(config, "SAAS_PUBLIC_URL", "http://plutus-homelab.test")
    db.migrate()
    from app.main import app

    with TestClient(app) as client:
        yield client


def _tiny_jpeg() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (40, 30), color=(100, 150, 200)).save(buf, format="JPEG")
    return buf.getvalue()


def _homelab_run() -> int:
    homelab.ensure_bootstrap()
    from app import service

    folder = config.DATA_DIR / "g"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "a.jpg").write_bytes(_tiny_jpeg())
    result = service.analyze_folder(
        folder, name="Homelab demo", tenant_id=homelab.tenant_id()
    )
    return result["run_id"]


def test_homelab_bootstrap_claims_orphan_runs(homelab_client):
    gid = db.insert_gallery(name="orphan", source="/x", photo_count=1, tenant_id=None)
    rid = db.insert_run(
        gallery_id=gid,
        engine="mock",
        bundle_count=1,
        estimated_total_cents=1000,
        payload={"bundles": [{"title": "A", "items": []}]},
        tenant_id=None,
    )
    homelab.ensure_bootstrap()
    row = db.get_run(rid)
    assert row is not None
    assert row["tenant_id"] == homelab.tenant_id()


def test_homelab_share_link_api_with_admin_bearer(homelab_client):
    run_id = _homelab_run()
    r = homelab_client.post(
        "/storefront/share-links",
        headers={"Authorization": "Bearer admin-secret"},
        data={"run_id": run_id, "label": "Client offer"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["public_url"] == f"http://plutus-homelab.test/store/studio/offer/{body['token']}"


def test_homelab_ui_share_link_and_order_page(homelab_client):
    run_id = _homelab_run()
    r = homelab_client.post(
        "/ui/homelab/share-link",
        data={"run_id": run_id, "label": "Wedding"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert f"/runs/{run_id}" in r.headers["location"]
    assert "share_created=1" in r.headers["location"]

    prepared = orders.prepare_bundle_order(
        tenant_id=homelab.tenant_id(),
        run_id=run_id,
        bundle_index=0,
        client_email="client@homelab.test",
    )
    order_id = prepared["order_id"]

    pay = homelab_client.post(
        f"/orders/{order_id}/simulate-payment",
        headers={"Authorization": "Bearer admin-secret"},
    )
    assert pay.status_code == 200
    assert pay.json()["status"] == "paid"
    assert pay.json()["lab_status"] == "submitted"

    page = homelab_client.get(f"/ui/homelab/orders/{order_id}")
    assert page.status_code == 200
    assert b"submitted" in page.content


def test_mise_gallery_recommend_uses_homelab_tenant(homelab_client, monkeypatch):
    from unittest.mock import patch

    from app import mise_client

    monkeypatch.setattr(mise_client, "is_enabled", lambda: True)
    monkeypatch.setattr(
        mise_client,
        "get_gallery",
        lambda _gid: {
            "id": 1,
            "published": True,
            "title": "Demo",
            "originals_path": "/tmp/g1/original",
            "argus_last_run_id": 99,
        },
    )
    folder = config.DATA_DIR / "mise1" / "original"
    folder.mkdir(parents=True)
    (folder / "a.jpg").write_bytes(_tiny_jpeg())
    monkeypatch.setattr(
        "app.service._resolve_folder",
        lambda **_: folder,
    )
    with patch("app.service.analyze_folder") as analyze:
        analyze.return_value = {"run_id": 42, "bundles": [{}]}
        r = homelab_client.post(
            "/recommend/mise-gallery",
            headers={"Authorization": "Bearer admin-secret"},
            data={"mise_gallery_id": 1, "argus_run_id": 99},
        )
    assert r.status_code == 200
    assert analyze.call_args.kwargs["tenant_id"] == homelab.tenant_id()


def test_homelab_health_includes_billing_and_lab(homelab_client):
    r = homelab_client.get("/healthz")
    assert r.status_code == 200
    body = r.json()
    assert body["homelab_store"] is True
    assert "billing" in body["checks"]
    assert "lab" in body["checks"]
    assert body["checks"]["lab"]["enabled"] is True