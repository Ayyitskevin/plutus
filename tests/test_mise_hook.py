"""Mise gallery recommend — studio admin API (PLUTUS_API_TOKEN bearer)."""
from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app import config, db


@pytest.fixture()
def studio_client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(config, "SAAS_MODE", False)
    monkeypatch.setattr(config, "API_TOKEN", "studio-admin")
    monkeypatch.setattr(config, "PUBLIC_URL", "http://plutus.test")
    monkeypatch.setattr(config, "RATE_LIMIT_ENABLED", False)
    monkeypatch.setattr(config, "MISE_MEDIA_ROOT", tmp_path / "mise-media")
    db.migrate()
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


def test_recommend_mise_gallery_with_api_token(studio_client, tmp_path):
    row = _gallery_folder(tmp_path)
    with patch("app.mise_client.get_gallery", return_value=row):
        with patch("app.mise_client.is_enabled", return_value=True):
            r = studio_client.post(
                "/recommend/mise-gallery",
                data={"mise_gallery_id": 3, "argus_run_id": 42},
                headers={"Authorization": "Bearer studio-admin"},
            )
    assert r.status_code == 200
    body = r.json()
    assert body["run_id"] >= 1
    assert body["review_url"] == f"http://plutus.test/runs/{body['run_id']}"
    assert body["pitch_url"] == f"http://plutus.test/runs/{body['run_id']}/pitch.txt"
    run = db.get_run(body["run_id"])
    assert run is not None


def test_recommend_mise_gallery_rejects_bad_token(studio_client):
    r = studio_client.post(
        "/recommend/mise-gallery",
        data={"mise_gallery_id": 1},
        headers={"Authorization": "Bearer wrong"},
    )
    assert r.status_code == 401


def test_recommend_mise_gallery_rejects_missing_token(studio_client):
    r = studio_client.post(
        "/recommend/mise-gallery",
        data={"mise_gallery_id": 1},
    )
    assert r.status_code == 401


def test_recommend_mise_gallery_accepts_hook_token(studio_client, tmp_path, monkeypatch):
    """Flow Mise posts the fleet hook secret (not the admin API token)."""
    monkeypatch.setattr(config, "MISE_HOOK_TOKEN", "fleet-hook-secret")
    row = _gallery_folder(tmp_path)
    with patch("app.mise_client.get_gallery", return_value=row):
        with patch("app.mise_client.is_enabled", return_value=True):
            r = studio_client.post(
                "/recommend/mise-gallery",
                data={"mise_gallery_id": 3},
                headers={"Authorization": "Bearer fleet-hook-secret"},
            )
    assert r.status_code == 200
    assert r.json()["run_id"] >= 1


def test_mise_webhook_not_registered_in_studio_mode(studio_client):
    """Legacy SaaS webhook path is unwired in studio-only deployments."""
    r = studio_client.post(
        "/webhooks/mise/gallery-published",
        data={"mise_gallery_id": 1},
        headers={"Authorization": "Bearer studio-admin"},
    )
    assert r.status_code == 404