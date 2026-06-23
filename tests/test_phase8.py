"""Phase 8 — polish: failed batch retry, health worker, gallery_theme in runs."""
from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app import config, db, service, tenants, upload_worker


@pytest.fixture()
def saas_client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(config, "SAAS_MODE", True)
    monkeypatch.setattr(config, "API_TOKEN", "admin-secret")
    monkeypatch.setattr(config, "TENANT_KEY_PEPPER", "pepper-secret")
    monkeypatch.setattr(config, "RATE_LIMIT_ENABLED", False)
    monkeypatch.setattr(config, "UPLOAD_ASYNC_ANALYZE", True)
    monkeypatch.setattr(config, "ARGUS_AUTO_VISION", False)
    db.migrate()
    from app.main import app

    return TestClient(app)


def _tiny_jpeg() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (40, 30), color=(100, 150, 200)).save(buf, format="JPEG")
    return buf.getvalue()


def test_failed_batch_can_retry_enqueue(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.db")
    db.migrate()
    tenants.create_tenant("retry", name="Retry", store_slug="retry")
    from app import uploads

    batch = uploads.create_batch(tenant_id="retry", name="Failed")
    batch_id = batch["id"]
    uploads.add_files(tenant_id="retry", batch_id=batch_id, files=[("a.jpg", _tiny_jpeg())])
    db.update_upload_batch(batch_id, status="failed", analyze_error="Argus timeout")

    result = service.enqueue_upload_batch_analyze(batch_id, tenant_id="retry")
    assert result["queued"] is True
    assert result["status"] == "queued"

    updated = db.get_upload_batch(batch_id, tenant_id="retry")
    assert updated is not None
    assert updated["status"] == "queued"
    assert updated["analyze_error"] is None


def test_failed_batch_retry_via_worker(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(config, "ARGUS_AUTO_VISION", False)
    db.migrate()
    tenants.create_tenant("rw", name="RW", store_slug="rw")
    from app import uploads

    batch = uploads.create_batch(tenant_id="rw", name="Retry worker")
    batch_id = batch["id"]
    uploads.add_files(tenant_id="rw", batch_id=batch_id, files=[("a.jpg", _tiny_jpeg())])
    db.update_upload_batch(batch_id, status="failed", analyze_error="simulated failure")
    service.enqueue_upload_batch_analyze(batch_id, tenant_id="rw")

    assert upload_worker.process_pending_batches() == 1
    done = db.get_upload_batch(batch_id, tenant_id="rw")
    assert done is not None
    assert done["status"] == "analyzed"
    assert done["run_id"]


def test_list_runs_includes_gallery_theme(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.db")
    db.migrate()
    tenants.create_tenant("theme", name="Theme", store_slug="theme")
    gid = db.insert_gallery(name="Food shoot", source="/x", photo_count=2, tenant_id="theme")
    db.insert_run(
        gallery_id=gid,
        engine="vision",
        bundle_count=2,
        estimated_total_cents=5000,
        payload={"bundles": [], "gallery_theme": "food", "photo_count": 2},
        tenant_id="theme",
    )
    runs = db.list_runs(tenant_id="theme", limit=5)
    assert runs[0]["gallery_theme"] == "food"
    assert runs[0]["engine"] == "vision"


def test_health_includes_upload_worker(saas_client, monkeypatch):
    monkeypatch.setattr(config, "UPLOAD_ASYNC_ANALYZE", True)
    r = saas_client.get("/healthz")
    assert r.status_code == 200
    body = r.json()
    worker = body["checks"]["upload_worker"]
    assert worker["enabled"] is True
    assert "queued" in worker