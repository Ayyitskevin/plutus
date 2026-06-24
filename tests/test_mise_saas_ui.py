"""SaaS Mise gallery browser — UI and API."""
from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app import config, db, tenants


@pytest.fixture()
def saas_client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(config, "SAAS_MODE", True)
    monkeypatch.setattr(config, "API_TOKEN", "admin-secret")
    monkeypatch.setattr(config, "TENANT_KEY_PEPPER", "pepper-secret")
    monkeypatch.setattr(config, "RATE_LIMIT_ENABLED", False)
    monkeypatch.setattr(config, "MISE_URL", "http://mise.test")
    monkeypatch.setattr(config, "MISE_API_TOKEN", "mise-token")
    monkeypatch.setattr(config, "MISE_MEDIA_ROOT", tmp_path / "mise-media")
    db.migrate()
    from app.main import app

    return TestClient(app)


def _login(client: TestClient, api_key: str) -> None:
    r = client.post(
        "/ui/saas/login",
        data={"api_token": api_key},
        follow_redirects=False,
    )
    assert r.status_code == 303


def _tenant_session(client: TestClient) -> tuple[str, str]:
    tenants.create_tenant("miseui", name="Mise UI Studio", store_slug="mise-ui")
    api_key = tenants.issue_api_key("miseui", label="ui")["api_key"]
    _login(client, api_key)
    return "miseui", api_key


def test_api_mise_galleries_requires_bearer(saas_client):
    r = saas_client.get("/api/mise/galleries")
    assert r.status_code == 401


def test_api_mise_galleries_lists_published(saas_client):
    galleries = {
        "galleries": [
            {"id": 1, "title": "Menu", "published": True, "argus_last_status": "done"},
        ]
    }
    with patch("app.mise_client.list_galleries", return_value=galleries):
        _, key = _tenant_session(saas_client)
        r = saas_client.get(
            "/api/mise/galleries?published=true",
            headers={"Authorization": f"Bearer {key}"},
        )
    assert r.status_code == 200
    assert r.json()["galleries"][0]["id"] == 1


def test_mise_ui_lists_galleries(saas_client):
    galleries = {
        "galleries": [
            {
                "id": 3,
                "title": "Wedding",
                "published": True,
                "argus_last_status": "done",
                "plutus_last_status": None,
            },
        ]
    }
    with patch("app.mise_client.list_galleries", return_value=galleries):
        _tenant_session(saas_client)
        r = saas_client.get("/ui/saas/app/mise")
    assert r.status_code == 200
    assert b"Wedding" in r.content
    assert b"Generate bundles" in r.content


def test_mise_recommend_redirects_to_run(saas_client, tmp_path):
    gid = 5
    folder = tmp_path / "mise-media" / str(gid) / "original"
    folder.mkdir(parents=True)
    from PIL import Image

    Image.new("RGB", (60, 40)).save(folder / "a.jpg")
    row = {
        "id": gid,
        "title": "Tasting",
        "published": True,
        "originals_path": str(folder),
        "argus_last_run_id": None,
    }
    with patch("app.mise_client.get_gallery", return_value=row):
        with patch("app.mise_client.is_enabled", return_value=True):
            _tenant_session(saas_client)
            r = saas_client.post(f"/ui/saas/app/mise/{gid}/recommend", follow_redirects=False)
    assert r.status_code == 303
    assert "recommended=1" in r.headers["location"]
    assert "run_id=" in r.headers["location"]