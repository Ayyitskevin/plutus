"""Tenant self-serve settings on dashboard."""
from __future__ import annotations

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


def test_tenant_can_update_notify_email(saas_client):
    tenants.create_tenant("setco", name="Set Co", store_slug="set-co")
    db.update_tenant("setco", email_verified_at="2026-01-01T00:00:00+00:00")
    api_key = tenants.issue_api_key("setco")["api_key"]
    _login(saas_client, api_key)

    r = saas_client.post(
        "/ui/saas/app/settings",
        data={"notify_email": "ops@setco.test"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert "settings_saved=1" in r.headers["location"]

    tenant = db.get_tenant("setco")
    assert tenant["notify_email"] == "ops@setco.test"

    page = saas_client.get("/ui/saas/app?settings_saved=1")
    assert b'value="ops@setco.test"' in page.content
    assert b"Notification email saved" in page.content