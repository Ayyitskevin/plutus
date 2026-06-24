"""Phase 5 — tenant gallery uploads (local/S3) and WHCC lab adapter."""
from __future__ import annotations

import io
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app import config, db, lab, lab_whcc, storage, tenants, uploads


@pytest.fixture()
def saas_client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(config, "SAAS_MODE", True)
    monkeypatch.setattr(config, "API_TOKEN", "admin-secret")
    monkeypatch.setattr(config, "TENANT_KEY_PEPPER", "pepper-secret")
    monkeypatch.setattr(config, "RATE_LIMIT_ENABLED", False)
    monkeypatch.setattr(config, "UPLOAD_ASYNC_ANALYZE", False)
    monkeypatch.setattr(config, "STORAGE_BACKEND", "local")
    monkeypatch.setattr(config, "LAB_ADAPTER", "whcc")
    monkeypatch.setattr(config, "WHCC_API_URL", "")
    monkeypatch.setattr(config, "WHCC_API_KEY", None)
    db.migrate()
    from app.main import app

    return TestClient(app)


def _tiny_jpeg() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (40, 30), color=(100, 150, 200)).save(buf, format="JPEG")
    return buf.getvalue()


def _paid_order(*, tenant_id: str = "t1") -> int:
    tenants.create_tenant(tenant_id, name="Studio", store_slug=tenant_id)
    gid = db.insert_gallery(name="g", source="/x", photo_count=1, tenant_id=tenant_id)
    rid = db.insert_run(
        gallery_id=gid,
        engine="mock",
        bundle_count=1,
        estimated_total_cents=1000,
        payload={"bundles": []},
        tenant_id=tenant_id,
    )
    oid = db.create_order(
        tenant_id=tenant_id,
        run_id=rid,
        bundle_index=0,
        total_cents=4500,
        items=[{"sku": "print-8x10", "label": "8x10", "quantity": 1, "unit_cents": 4500}],
    )
    paid_at = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
    db.update_order(oid, status="paid", paid_at=paid_at)
    return oid


def test_upload_batch_local_storage_and_analyze(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(config, "STORAGE_BACKEND", "local")
    monkeypatch.setattr(config, "SAAS_MODE", False)
    monkeypatch.setattr(config, "UPLOAD_ASYNC_ANALYZE", False)
    db.migrate()
    tenants.create_tenant("u1", name="Upload Studio", store_slug="u1")

    batch = uploads.create_batch(tenant_id="u1", name="Wedding")
    batch_id = batch["id"]

    updated = uploads.add_files(
        tenant_id="u1",
        batch_id=batch_id,
        files=[("photo1.jpg", _tiny_jpeg()), ("photo2.jpg", _tiny_jpeg())],
    )
    assert updated["photo_count"] == 2
    assert updated["status"] == "ready"
    assert len(storage.list_gallery_uris("u1", batch_id)) == 2

    from app import service

    result = service.analyze_upload_batch(batch_id, tenant_id="u1")
    assert result["run_id"]

    batch_after = db.get_upload_batch(batch_id, tenant_id="u1")
    assert batch_after is not None
    assert batch_after["status"] == "analyzed"
    assert batch_after["run_id"] == result["run_id"]


def test_recommend_upload_batch_api(saas_client):
    tenants.create_tenant("api", name="API Studio", store_slug="api")
    issued = tenants.issue_api_key("api")
    api_key = issued["api_key"]

    batch = uploads.create_batch(tenant_id="api", name="Session")
    batch_id = batch["id"]
    uploads.add_files(
        tenant_id="api",
        batch_id=batch_id,
        files=[("a.jpg", _tiny_jpeg())],
    )

    r = saas_client.post(
        "/recommend/upload-batch",
        headers={"Authorization": f"Bearer {api_key}"},
        data={"batch_id": batch_id},
    )
    assert r.status_code == 200
    assert "run_id" in r.json()


def test_ui_upload_redirects(saas_client):
    tenants.create_tenant("ui", name="UI Studio", store_slug="ui")
    issued = tenants.issue_api_key("ui")
    api_key = issued["api_key"]

    r = saas_client.post(
        "/ui/saas/login",
        data={"api_token": api_key},
        follow_redirects=False,
    )
    assert r.status_code == 303

    r = saas_client.post(
        "/ui/saas/app/upload",
        data={"gallery_name": "Portrait Session"},
        files=[("files", ("portrait.jpg", _tiny_jpeg(), "image/jpeg"))],
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert "uploaded=1" in r.headers["location"]

    batches = db.list_upload_batches(tenant_id="ui", limit=5)
    assert len(batches) == 1
    assert batches[0]["photo_count"] == 1


def test_whcc_stub_submit_and_poll(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(config, "LAB_ADAPTER", "whcc")
    monkeypatch.setattr(config, "WHCC_API_URL", "")
    monkeypatch.setattr(config, "WHCC_API_KEY", None)
    db.migrate()

    oid = _paid_order()
    result = lab.submit_order(oid)
    assert result["lab_status"] == "submitted"
    assert result["lab_ref"].startswith("whcc-stub-")

    poll = lab.poll_order(oid)
    assert poll["advanced"] is True
    assert poll["lab_status"] == "processing"

    events = db.list_fulfillment_events(oid)
    assert any(e["status"] == "submitted" for e in events)
    assert any(e["status"] == "processing" for e in events)


def test_whcc_webhook_updates_order(saas_client, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(config, "WHCC_WEBHOOK_SECRET", "whcc-secret")
    db.migrate()

    oid = _paid_order(tenant_id="wh")
    ref = "whcc-stub-wh-abc123"
    db.update_order(oid, lab_status="processing", lab_ref=ref)

    from app import lab_whcc

    body = f'{{"order_id":"{ref}","status":"shipped"}}'.encode()
    sig = lab_whcc.whcc_webhook_signature(body, secret="whcc-secret")
    r = saas_client.post(
        "/webhooks/whcc",
        headers={"X-WHCC-Signature": f"sha256={sig}"},
        content=body,
    )
    assert r.status_code == 200
    assert r.json()["received"] is True

    order = db.get_order(oid)
    assert order is not None
    assert order["lab_status"] == "shipped"


def test_whcc_webhook_rejects_bad_auth(saas_client, monkeypatch):
    monkeypatch.setattr(config, "WHCC_WEBHOOK_SECRET", "whcc-secret")
    r = saas_client.post(
        "/webhooks/whcc",
        headers={"Authorization": "Bearer wrong"},
        json={"order_id": "x", "status": "shipped"},
    )
    assert r.status_code == 401


def test_s3_storage_with_mocked_boto(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(config, "STORAGE_BACKEND", "s3")
    monkeypatch.setattr(config, "S3_BUCKET", "test-bucket")
    monkeypatch.setattr(config, "S3_ACCESS_KEY", "key")
    monkeypatch.setattr(config, "S3_SECRET_KEY", "secret")
    monkeypatch.setattr(config, "S3_PREFIX", "plutus/tenants")
    db.migrate()
    tenants.create_tenant("s3", name="S3 Studio", store_slug="s3")

    stored_objects: dict[str, bytes] = {}
    mock_client = MagicMock()

    def put_object(**kwargs):
        stored_objects[kwargs["Key"]] = kwargs["Body"]

    def upload_fileobj(fileobj, bucket, key, **kwargs):
        del bucket, kwargs
        stored_objects[key] = fileobj.read()

    def list_objects_v2(**kwargs):
        prefix = kwargs["Prefix"]
        keys = [k for k in stored_objects if k.startswith(prefix)]
        return {
            "Contents": [{"Key": k} for k in keys],
            "IsTruncated": False,
        }

    def get_object(**kwargs):
        key = kwargs["Key"]
        payload = stored_objects[key]
        body = MagicMock()
        body.read.side_effect = [payload, b""]
        return {"Body": body}

    mock_client.put_object.side_effect = put_object
    mock_client.upload_fileobj.side_effect = upload_fileobj
    mock_client.list_objects_v2.side_effect = list_objects_v2
    mock_client.get_object.side_effect = get_object

    with patch("app.storage._s3_client", return_value=mock_client):
        batch = uploads.create_batch(tenant_id="s3", name="Cloud Gallery")
        batch_id = batch["id"]
        uploads.add_files(
            tenant_id="s3",
            batch_id=batch_id,
            files=[("cloud.jpg", _tiny_jpeg())],
        )
        uris = storage.list_gallery_uris("s3", batch_id)
        assert len(uris) == 1
        assert uris[0].startswith("s3://test-bucket/")

        folder = storage.prepare_gallery_folder("s3", batch_id)
        assert folder.is_dir()
        assert any(p.suffix.lower() == ".jpg" for p in folder.iterdir())


def test_health_includes_storage_in_saas_mode(saas_client):
    r = saas_client.get("/healthz")
    assert r.status_code == 200
    body = r.json()
    assert "storage" in body["checks"]
    assert body["checks"]["storage"]["backend"] == "local"
    assert "argus" in body["checks"]


def test_health_argus_reachable(saas_client, monkeypatch):
    monkeypatch.setattr(config, "ARGUS_URL", "http://argus.test")
    monkeypatch.setattr(config, "ARGUS_TOKEN", "secret")

    from app import argus_client

    monkeypatch.setattr(
        argus_client,
        "vision_status",
        lambda: {
            "configured": True,
            "reachable": True,
            "backend": "grok",
            "provider": "xai",
        },
    )
    r = saas_client.get("/healthz")
    body = r.json()
    assert body["checks"]["argus"]["status"] == "ok"
    assert body["checks"]["argus"]["provider"] == "xai"


def test_auto_argus_vision_on_upload_batch(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(config, "STORAGE_BACKEND", "local")
    monkeypatch.setattr(config, "ARGUS_URL", "http://argus.test")
    monkeypatch.setattr(config, "ARGUS_TOKEN", "secret")
    monkeypatch.setattr(config, "ARGUS_AUTO_VISION", True)
    monkeypatch.setattr(config, "SAAS_MODE", False)
    monkeypatch.setattr(config, "UPLOAD_ASYNC_ANALYZE", False)
    db.migrate()
    tenants.create_tenant("vision", name="Vision Studio", store_slug="vision")

    batch = uploads.create_batch(tenant_id="vision", name="Auto Argus")
    batch_id = batch["id"]
    uploads.add_files(
        tenant_id="vision",
        batch_id=batch_id,
        files=[("food.jpg", _tiny_jpeg())],
    )

    calls: list[dict] = []

    def fake_analyze(folder, *, limit=None, client_id=None):
        calls.append({"folder": folder, "limit": limit, "client_id": client_id})
        return 42

    from app import argus_client, service

    monkeypatch.setattr(argus_client, "analyze_folder", fake_analyze)
    monkeypatch.setattr(service.ingest, "enrich_from_argus_run", lambda photos, run_id: photos)

    result = service.analyze_upload_batch(batch_id, tenant_id="vision")
    assert result["argus_run_id"] == 42
    assert calls[0]["client_id"] == "plutus:vision"


def test_auto_argus_skipped_when_disabled(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(config, "ARGUS_AUTO_VISION", False)
    monkeypatch.setattr(config, "SAAS_MODE", False)
    monkeypatch.setattr(config, "UPLOAD_ASYNC_ANALYZE", False)
    monkeypatch.setattr(config, "ARGUS_URL", "http://argus.test")
    monkeypatch.setattr(config, "ARGUS_TOKEN", "secret")
    db.migrate()
    tenants.create_tenant("off", name="Off", store_slug="off")

    batch = uploads.create_batch(tenant_id="off", name="No Argus")
    batch_id = batch["id"]
    uploads.add_files(tenant_id="off", batch_id=batch_id, files=[("a.jpg", _tiny_jpeg())])

    from app import argus_client, service

    def fail_analyze(*_a, **_k):
        raise AssertionError("Argus should not be called")

    monkeypatch.setattr(argus_client, "analyze_folder", fail_analyze)
    result = service.analyze_upload_batch(batch_id, tenant_id="off")
    assert result.get("argus_run_id") is None


def test_lab_whcc_handle_webhook_direct(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.db")
    db.migrate()

    oid = _paid_order(tenant_id="hook")
    ref = "whcc-live-99"
    db.update_order(oid, lab_status="submitted", lab_ref=ref)

    assert lab_whcc.handle_webhook({"order_id": ref, "status": "in_production"}) is True
    order = db.get_order(oid)
    assert order is not None
    assert order["lab_status"] == "processing"