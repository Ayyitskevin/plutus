"""Trust surface — privacy and terms pages."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import config, db


@pytest.fixture()
def saas_client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(config, "SAAS_MODE", True)
    monkeypatch.setattr(config, "API_TOKEN", "admin-secret")
    monkeypatch.setattr(config, "RATE_LIMIT_ENABLED", False)
    db.migrate()
    from app.main import app

    return TestClient(app)


def test_privacy_page_is_public(saas_client: TestClient):
    r = saas_client.get("/privacy")
    assert r.status_code == 200
    assert "do not train" in r.text.lower()
    assert "tenant" in r.text.lower()


def test_terms_page_is_public_and_links_privacy(saas_client: TestClient):
    r = saas_client.get("/terms")
    assert r.status_code == 200
    assert "terms of service" in r.text.lower()
    assert 'href="/privacy"' in r.text


def test_saas_landing_links_trust_pages(saas_client: TestClient):
    r = saas_client.get("/ui/saas")
    assert r.status_code == 200
    assert 'href="/privacy"' in r.text
    assert 'href="/terms"' in r.text