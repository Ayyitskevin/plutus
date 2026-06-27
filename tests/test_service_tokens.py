"""PR2 — the inbound service-token register (kills the recurring 401 drift)."""
from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app import config, db, service_tokens


@pytest.fixture()
def studio_client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(config, "SAAS_MODE", False)
    monkeypatch.setattr(config, "API_TOKEN", "studio-admin")
    monkeypatch.setattr(config, "MISE_HOOK_TOKEN", None)
    monkeypatch.setattr(config, "SERVICE_TOKENS", [])
    monkeypatch.setattr(config, "PUBLIC_URL", "http://plutus.test")
    monkeypatch.setattr(config, "RATE_LIMIT_ENABLED", False)
    monkeypatch.setattr(config, "MISE_MEDIA_ROOT", tmp_path / "mise-media")
    db.migrate()
    from app.main import app

    return TestClient(app)


# --- register unit behavior ---------------------------------------------------


def test_register_aggregates_and_dedupes(monkeypatch):
    monkeypatch.setattr(config, "API_TOKEN", "a")
    monkeypatch.setattr(config, "MISE_HOOK_TOKEN", "b")
    monkeypatch.setattr(config, "SERVICE_TOKENS", ["b", "c"])  # 'b' duplicates hook
    assert service_tokens.registered_tokens() == ["a", "b", "c"]
    assert service_tokens.auth_required() is True


def test_verify_accepts_any_registered_token(monkeypatch):
    monkeypatch.setattr(config, "API_TOKEN", "admin")
    monkeypatch.setattr(config, "MISE_HOOK_TOKEN", "hook")
    monkeypatch.setattr(config, "SERVICE_TOKENS", ["rotation-new"])
    assert service_tokens.verify("admin")
    assert service_tokens.verify("hook")
    assert service_tokens.verify("rotation-new")
    assert not service_tokens.verify("nope")
    assert not service_tokens.verify(None)


def test_no_tokens_means_open(monkeypatch):
    monkeypatch.setattr(config, "API_TOKEN", "")
    monkeypatch.setattr(config, "MISE_HOOK_TOKEN", None)
    monkeypatch.setattr(config, "SERVICE_TOKENS", [])
    assert service_tokens.auth_required() is False
    assert service_tokens.verify(None) is True  # studio-dev default


# --- recommend path integration ----------------------------------------------


def _gallery_folder(tmp_path, gid: int = 3):
    folder = tmp_path / "mise-media" / str(gid) / "original"
    folder.mkdir(parents=True)
    Image.new("RGB", (80, 60)).save(folder / "a.jpg")
    return {
        "id": gid,
        "title": "Published",
        "published": True,
        "originals_path": str(folder),
        "argus_last_run_id": None,
    }


def _post(client, token=None):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return client.post(
        "/recommend/mise-gallery", data={"mise_gallery_id": 3}, headers=headers
    )


def test_recommend_accepts_rotation_token(studio_client, tmp_path, monkeypatch):
    """A new secret supplied via PLUTUS_SERVICE_TOKENS works without dropping the old."""
    monkeypatch.setattr(config, "SERVICE_TOKENS", ["rotation-new"])
    row = _gallery_folder(tmp_path)
    with patch("app.mise_client.get_gallery", return_value=row):
        with patch("app.mise_client.is_enabled", return_value=True):
            # Both the legacy admin token and the rotation token are accepted.
            assert _post(studio_client, "studio-admin").status_code == 200
            assert _post(studio_client, "rotation-new").status_code == 200


def test_recommend_rejects_unregistered_token(studio_client):
    assert _post(studio_client, "wrong").status_code == 401
    assert _post(studio_client, None).status_code == 401
