"""Tier 13 — Redis health, audit purge, fail-closed rate limits."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from app import audit_retention, config, db, health, metrics, rate_limit, redis_client


def test_purge_audit_events(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.db")
    db.migrate()
    old = (datetime.now(UTC) - timedelta(days=120)).isoformat()
    with db.connection() as con:
        con.execute(
            "INSERT INTO audit_log (created_at, action, status) VALUES (?, ?, ?)",
            (old, "test.old", "ok"),
        )
        con.execute(
            "INSERT INTO audit_log (created_at, action, status) VALUES (datetime('now'), ?, ?)",
            ("test.new", "ok"),
        )
    removed = db.purge_audit_events(older_than_days=90)
    assert removed == 1
    rows = db.list_audit_events(limit=10)
    assert len(rows) == 1
    assert rows[0]["action"] == "test.new"


def test_audit_retention_respects_config(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(config, "AUDIT_LOG_ENABLED", True)
    monkeypatch.setattr(config, "AUDIT_LOG_RETENTION_DAYS", 30)
    db.migrate()
    old = (datetime.now(UTC) - timedelta(days=60)).isoformat()
    with db.connection() as con:
        con.execute(
            "INSERT INTO audit_log (created_at, action, status) VALUES (?, ?, ?)",
            (old, "purge.me", "ok"),
        )
    assert audit_retention.purge_stale_audit_events() == 1


def test_health_includes_redis_when_saas(monkeypatch):
    monkeypatch.setattr(config, "SAAS_MODE", True)
    monkeypatch.setattr(config, "REDIS_URL", "redis://127.0.0.1:6379/0")
    redis_ok = {"status": "ok", "configured": True, "reachable": True}
    with patch.object(redis_client, "ping_status", return_value=redis_ok):
        report = health.build_health_report()
    assert report["checks"]["redis"]["status"] == "ok"


def test_rate_limit_no_memory_fallback_when_saas_redis_required(monkeypatch):
    monkeypatch.setattr(config, "SAAS_MODE", True)
    monkeypatch.setattr(config, "RATE_LIMIT_ENABLED", True)
    monkeypatch.setattr(config, "REDIS_URL", "redis://127.0.0.1:6379/0")
    with patch.object(redis_client, "get_client", return_value=None):
        ok, _, _, backend_down = rate_limit._check("tenant:t1", 60)
    assert ok is False
    assert backend_down is True


def test_saas_rate_limit_fails_closed_without_redis(monkeypatch):
    monkeypatch.setattr(config, "SAAS_MODE", True)
    monkeypatch.setattr(config, "RATE_LIMIT_ENABLED", True)
    monkeypatch.setattr(config, "REDIS_URL", None)
    with patch.object(redis_client, "get_client", return_value=None):
        ok, _, _, backend_down = rate_limit._check("ip:1.2.3.4", 60)
    assert ok is False
    assert backend_down is True


def test_prometheus_includes_upload_queue_gauges(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(config, "SAAS_MODE", True)
    db.migrate()
    from app import tenants

    tenants.create_tenant("gq", name="GQ", store_slug="gq")
    db.create_upload_batch(batch_id="q1", tenant_id="gq", name="Queued")
    text = metrics.prometheus_text()
    assert "plutus_upload_batches_queued" in text