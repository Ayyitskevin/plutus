"""M5 integration API — one-shot offer mint."""
from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app import config, tenants


@pytest.fixture()
def saas_client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(config, "SAAS_MODE", True)
    monkeypatch.setattr(config, "API_TOKEN", "admin-secret")
    monkeypatch.setattr(config, "TENANT_KEY_PEPPER", "pepper-secret")
    monkeypatch.setattr(config, "RATE_LIMIT_ENABLED", False)
    monkeypatch.setattr(config, "SAAS_PUBLIC_URL", "http://plutus.test")
    from app import db

    db.migrate()
    from app.main import app

    return TestClient(app)


def _tiny_jpeg() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (40, 30), color=(100, 150, 200)).save(buf, format="JPEG")
    return buf.getvalue()


def _tenant_run(tenant_id: str = "intco") -> tuple[str, int]:
    from app import service

    tenants.create_tenant(tenant_id, name="Integration Co", store_slug=tenant_id)
    issued = tenants.issue_api_key(tenant_id)
    folder = config.DATA_DIR / "g"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "a.jpg").write_bytes(_tiny_jpeg())
    run_id = service.analyze_folder(folder, name="Demo", tenant_id=tenant_id)["run_id"]
    return issued["api_key"], run_id


def test_integrations_offer_with_tenant_key(saas_client):
    api_key, run_id = _tenant_run("int1")
    r = saas_client.post(
        "/integrations/offer",
        headers={"Authorization": f"Bearer {api_key}"},
        data={"run_id": run_id, "label": "Client package"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["store_slug"] == "int1"
    assert f"/store/int1/offer/{body['token']}" in body["public_url"]


def test_integrations_offer_admin_with_tenant_id(saas_client, monkeypatch):
    monkeypatch.setattr(config, "MISE_HOOK_TENANT_ID", None)
    _, run_id = _tenant_run("int-admin")
    r = saas_client.post(
        "/integrations/offer",
        headers={"Authorization": "Bearer admin-secret"},
        data={"run_id": run_id, "tenant_id": "int-admin"},
    )
    assert r.status_code == 200
    assert r.json()["store_slug"] == "int-admin"