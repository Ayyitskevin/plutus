"""SaaS publish-and-sell wizard."""
from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app import config, db, sell, tenants


@pytest.fixture()
def saas_client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(config, "SAAS_MODE", True)
    monkeypatch.setattr(config, "API_TOKEN", "admin-secret")
    monkeypatch.setattr(config, "TENANT_KEY_PEPPER", "pepper-secret")
    monkeypatch.setattr(config, "RATE_LIMIT_ENABLED", False)
    monkeypatch.setattr(config, "UPLOAD_ASYNC_ANALYZE", False)
    monkeypatch.setattr(config, "SAAS_PUBLIC_URL", "http://plutus.test")
    db.migrate()
    from app.main import app

    return TestClient(app)


def _tiny_jpeg() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (40, 30), color=(100, 150, 200)).save(buf, format="JPEG")
    return buf.getvalue()


def _login(saas_client: TestClient, api_key: str) -> None:
    r = saas_client.post(
        "/ui/saas/login",
        data={"api_token": api_key},
        follow_redirects=False,
    )
    assert r.status_code == 303


def _tenant_with_run() -> tuple[str, int]:
    tenants.create_tenant("sellco", name="Sell Co", store_slug="sell-co")
    issued = tenants.issue_api_key("sellco")
    from app import service

    folder = config.DATA_DIR / "g"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "a.jpg").write_bytes(_tiny_jpeg())
    result = service.analyze_folder(folder, name="Demo gallery", tenant_id="sellco")
    return issued["api_key"], result["run_id"]


def test_sell_wizard_page_requires_login(saas_client):
    r = saas_client.get("/ui/saas/app/sell", follow_redirects=False)
    assert r.status_code == 303
    assert "/ui/saas/login" in r.headers["location"]


def test_publish_and_sell_from_run(saas_client):
    api_key, run_id = _tenant_with_run()
    _login(saas_client, api_key)

    r = saas_client.get("/ui/saas/app/sell")
    assert r.status_code == 200
    assert b"Publish &amp; sell" in r.content

    r = saas_client.post(
        "/ui/saas/app/sell",
        data={"action": "publish_run", "run_id": run_id, "label": "Client offer"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert "published=1" in r.headers["location"]
    assert f"run_id={run_id}" in r.headers["location"]
    assert "offer_url=" in r.headers["location"]

    r = saas_client.get(r.headers["location"])
    assert r.status_code == 200
    assert b"Client offer ready" in r.content
    assert b"/store/sell-co/offer/" in r.content


def test_publish_and_sell_upload_flow(saas_client):
    tenants.create_tenant("uploadco", name="Upload Co", store_slug="upload-co")
    issued = tenants.issue_api_key("uploadco")
    _login(saas_client, issued["api_key"])

    r = saas_client.post(
        "/ui/saas/app/sell",
        data={"action": "upload_publish", "gallery_name": "Wedding", "label": "Wedding offer"},
        files=[("files", ("01.jpg", _tiny_jpeg(), "image/jpeg"))],
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert "published=1" in r.headers["location"]

    r = saas_client.get(r.headers["location"])
    assert r.status_code == 200
    assert b"/store/upload-co/offer/" in r.content


def test_sell_module_publish_and_sell(saas_client):
    tenants.create_tenant("modco", name="Mod Co", store_slug="mod-co")
    from app import service

    folder = config.DATA_DIR / "mod"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "a.jpg").write_bytes(_tiny_jpeg())
    run_id = service.analyze_folder(folder, name="Mod", tenant_id="modco")["run_id"]

    result = sell.publish_and_sell("modco", run_id=run_id, label="Test")
    assert result["run_id"] == run_id
    assert "/store/mod-co/offer/" in result["offer_url"]
    assert "steps" in result