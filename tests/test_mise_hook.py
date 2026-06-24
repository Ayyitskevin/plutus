"""Mise publish hook — webhook + admin tenant_id."""
from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app import config, db, tenants


@pytest.fixture()
def saas_client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(config, "SAAS_MODE", True)
    monkeypatch.setattr(config, "API_TOKEN", "hook-admin")
    monkeypatch.setattr(config, "MISE_HOOK_TOKEN", "hook-secret")
    monkeypatch.setattr(config, "MISE_HOOK_TENANT_ID", "flow-studio")
    monkeypatch.setattr(config, "TENANT_KEY_PEPPER", "pepper")
    monkeypatch.setattr(config, "RATE_LIMIT_ENABLED", False)
    monkeypatch.setattr(
        config, "MISE_MEDIA_ROOT", tmp_path / "mise-media"
    )
    db.migrate()
    tenants.create_tenant("flow-studio", name="Flow Studio", store_slug="flow-studio")
    from app.main import app

    return TestClient(app)


def _gallery_folder(tmp_path, gid: int = 3):
    folder = tmp_path / "mise-media" / str(gid) / "original"
    folder.mkdir(parents=True)
    Image.new("RGB", (80, 60)).save(folder / "a.jpg")
    return {
        "id": gid,
        "title": "Published",
        "published": True,
        "originals_path": str(folder),
        "argus_last_run_id": 42,
    }


def test_mise_webhook_recommends_for_hook_tenant(saas_client, tmp_path):
    row = _gallery_folder(tmp_path)
    with patch("app.mise_client.get_gallery", return_value=row):
        with patch("app.mise_client.is_enabled", return_value=True):
            r = saas_client.post(
                "/webhooks/mise/gallery-published",
                data={"mise_gallery_id": 3},
                headers={"Authorization": "Bearer hook-secret"},
            )
    assert r.status_code == 200
    body = r.json()
    assert body["run_id"] >= 1
    run = db.get_run(body["run_id"], tenant_id="flow-studio")
    assert run is not None


def test_admin_recommend_accepts_explicit_tenant_id(saas_client, tmp_path):
    tenants.create_tenant("other", name="Other", store_slug="other")
    row = _gallery_folder(tmp_path, gid=9)
    with patch("app.mise_client.get_gallery", return_value=row):
        with patch("app.mise_client.is_enabled", return_value=True):
            r = saas_client.post(
                "/recommend/mise-gallery",
                data={"mise_gallery_id": 9, "tenant_id": "other"},
                headers={"Authorization": "Bearer hook-admin"},
            )
    assert r.status_code == 200
    run = db.get_run(r.json()["run_id"], tenant_id="other")
    assert run is not None


def test_mise_webhook_rejects_bad_token(saas_client):
    r = saas_client.post(
        "/webhooks/mise/gallery-published",
        data={"mise_gallery_id": 1},
        headers={"Authorization": "Bearer wrong"},
    )
    assert r.status_code == 401


def test_mise_webhook_rejects_admin_token(saas_client):
    r = saas_client.post(
        "/webhooks/mise/gallery-published",
        data={"mise_gallery_id": 1},
        headers={"Authorization": "Bearer hook-admin"},
    )
    assert r.status_code == 401


def test_recommend_mise_gallery_accepts_hook_token(saas_client, tmp_path):
    """Flow Mise posts the hook secret to /recommend/mise-gallery (not the webhook)."""
    row = _gallery_folder(tmp_path)
    with patch("app.mise_client.get_gallery", return_value=row):
        with patch("app.mise_client.is_enabled", return_value=True):
            r = saas_client.post(
                "/recommend/mise-gallery",
                data={"mise_gallery_id": 3},
                headers={"Authorization": "Bearer hook-secret"},
            )
    assert r.status_code == 200
    body = r.json()
    assert body["run_id"] >= 1
    assert body.get("offer_url", "").startswith("http")
    run = db.get_run(body["run_id"], tenant_id="flow-studio")
    assert run is not None