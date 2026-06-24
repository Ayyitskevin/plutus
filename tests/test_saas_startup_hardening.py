"""SaaS startup guards — Redis rate limits and Mise hook tenant lockdown."""
from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image

from app import config, db, rate_limit, tenants


def _without_pytest(fn):
    saved = sys.modules.pop("pytest", None)
    try:
        fn()
    finally:
        if saved is not None:
            sys.modules["pytest"] = saved


def test_rate_limit_startup_requires_redis_url(monkeypatch):
    monkeypatch.setattr(config, "SAAS_MODE", True)
    monkeypatch.setattr(config, "RATE_LIMIT_ENABLED", True)
    monkeypatch.setattr(config, "REDIS_URL", None)

    def run():
        with pytest.raises(RuntimeError, match="PLUTUS_REDIS_URL required"):
            rate_limit.validate_rate_limit_backend()

    _without_pytest(run)


def test_rate_limit_startup_pings_redis(monkeypatch):
    monkeypatch.setattr(config, "SAAS_MODE", True)
    monkeypatch.setattr(config, "RATE_LIMIT_ENABLED", True)
    monkeypatch.setattr(config, "REDIS_URL", "redis://127.0.0.1:6379/0")

    mock_client = MagicMock()
    mock_redis = MagicMock()
    mock_redis.from_url.return_value = mock_client

    def run():
        with patch.dict(sys.modules, {"redis": mock_redis}):
            rate_limit.validate_rate_limit_backend()
        mock_redis.from_url.assert_called_once_with(
            "redis://127.0.0.1:6379/0", decode_responses=True
        )
        mock_client.ping.assert_called_once()

    _without_pytest(run)


def test_mise_webhook_ignores_spoofed_tenant_id(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(config, "SAAS_MODE", True)
    monkeypatch.setattr(config, "API_TOKEN", "hook-admin")
    monkeypatch.setattr(config, "MISE_HOOK_TOKEN", "hook-secret")
    monkeypatch.setattr(config, "MISE_HOOK_TENANT_ID", "flow-studio")
    monkeypatch.setattr(config, "TENANT_KEY_PEPPER", "pepper")
    monkeypatch.setattr(config, "RATE_LIMIT_ENABLED", False)
    monkeypatch.setattr(config, "MISE_MEDIA_ROOT", tmp_path / "mise-media")
    db.migrate()
    tenants.create_tenant("flow-studio", name="Flow Studio", store_slug="flow-studio")
    tenants.create_tenant("attacker", name="Attacker", store_slug="attacker")

    folder = tmp_path / "mise-media" / "7" / "original"
    folder.mkdir(parents=True)
    Image.new("RGB", (80, 60)).save(folder / "a.jpg")
    row = {
        "id": 7,
        "title": "Published",
        "published": True,
        "originals_path": str(folder),
        "argus_last_run_id": 42,
    }

    from app.main import app
    from fastapi.testclient import TestClient

    client = TestClient(app)
    with patch("app.mise_client.get_gallery", return_value=row):
        with patch("app.mise_client.is_enabled", return_value=True):
            r = client.post(
                "/webhooks/mise/gallery-published",
                data={"mise_gallery_id": 7, "tenant_id": "attacker"},
                headers={"Authorization": "Bearer hook-secret"},
            )
    assert r.status_code == 200
    run = db.get_run(r.json()["run_id"], tenant_id="flow-studio")
    assert run is not None
    assert db.get_run(r.json()["run_id"], tenant_id="attacker") is None