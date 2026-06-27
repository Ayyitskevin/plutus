"""/healthz exposes the contract/version surface Mise monitors."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import config, db, offer_schema, recommend


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(config, "API_TOKEN", "studio-admin")
    monkeypatch.setattr(config, "RATE_LIMIT_ENABLED", False)
    db.migrate()
    from app.main import app

    return TestClient(app)


def test_healthz_reports_schema_and_model_version(client):
    body = client.get("/healthz").json()
    assert body["service"] == "plutus"
    assert body["offer_schema_version"] == offer_schema.OFFER_SCHEMA_VERSION
    assert body["model"] == recommend.MODEL_VERSION
    assert body["auth_enabled"] is True
    assert "status" in body and "checks" in body
