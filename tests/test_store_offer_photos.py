"""Client offer page — photo thumbnails on storefront."""
from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app import config, db, tenants
from app.storefront import create_share_link


@pytest.fixture()
def saas_client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(config, "SAAS_MODE", True)
    monkeypatch.setattr(config, "API_TOKEN", "admin-secret")
    monkeypatch.setattr(config, "TENANT_KEY_PEPPER", "pepper-secret")
    monkeypatch.setattr(config, "RATE_LIMIT_ENABLED", False)
    monkeypatch.setattr(config, "SAAS_PUBLIC_URL", "http://plutus.test")
    db.migrate()
    from app.main import app

    return TestClient(app)


def _tiny_jpeg(color: tuple[int, int, int] = (120, 80, 200)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (120, 90), color=color).save(buf, format="JPEG")
    return buf.getvalue()


def _tenant_offer(client: TestClient, tenant_id: str = "studio") -> tuple[str, dict]:
    tenants.create_tenant(tenant_id, name="Studio", store_slug=tenant_id)
    issued = tenants.issue_api_key(tenant_id)
    folder = config.DATA_DIR / "gallery"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "hero.jpg").write_bytes(_tiny_jpeg((200, 40, 40)))
    (folder / "detail.jpg").write_bytes(_tiny_jpeg((40, 180, 90)))
    from app import service

    result = service.analyze_folder(folder, name="Client Gallery", tenant_id=tenant_id)
    link = create_share_link(tenant_id=tenant_id, run_id=result["run_id"])
    return issued["api_key"], link


def test_store_offer_renders_photo_thumbnails(saas_client):
    _, link = _tenant_offer(saas_client)
    r = saas_client.get(link["url"])
    assert r.status_code == 200
    assert b"/photo/hero.jpg" in r.content
    assert b"bundle-hero" in r.content
    assert b"bundle-item" in r.content


def test_offer_photo_endpoint_serves_jpeg(saas_client):
    _, link = _tenant_offer(saas_client)
    url = f"{link['url']}/photo/hero.jpg"
    r = saas_client.get(url)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("image/jpeg")
    assert len(r.content) > 100
    img = Image.open(io.BytesIO(r.content))
    assert max(img.size) <= 520


def test_offer_photo_rejects_unknown_filename(saas_client):
    _, link = _tenant_offer(saas_client)
    r = saas_client.get(f"{link['url']}/photo/not-in-bundle.jpg")
    assert r.status_code == 404


def test_offer_photo_rejects_path_traversal(saas_client):
    _, link = _tenant_offer(saas_client)
    r = saas_client.get(f"{link['url']}/photo/..%2Fhero.jpg")
    assert r.status_code == 404