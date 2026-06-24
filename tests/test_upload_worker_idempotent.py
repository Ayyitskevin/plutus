"""Upload worker must not double-analyze batches that already have a run_id."""
from __future__ import annotations

import io

import pytest
from PIL import Image

from app import config, db, service, tenants, uploads


def _tiny_jpeg() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (8, 8)).save(buf, format="JPEG")
    return buf.getvalue()


@pytest.fixture()
def batch_env(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.db")
    db.migrate()
    tenants.create_tenant("idem", name="Idem", store_slug="idem")
    batch = uploads.create_batch(tenant_id="idem", name="Retry")
    uploads.add_files(
        tenant_id="idem",
        batch_id=batch["id"],
        files=[("a.jpg", _tiny_jpeg())],
    )
    gid = db.insert_gallery(name="g", source="/x", photo_count=1, tenant_id="idem")
    rid = db.insert_run(
        gallery_id=gid,
        engine="mock",
        bundle_count=1,
        estimated_total_cents=100,
        payload={"bundles": []},
        tenant_id="idem",
    )
    db.update_upload_batch(batch["id"], status="analyzing", run_id=rid)
    return batch["id"], rid


def test_process_upload_batch_skips_when_run_id_set(batch_env, monkeypatch):
    batch_id, run_id = batch_env
    calls = {"n": 0}
    original = service.analyze_folder

    def spy(*args, **kwargs):
        calls["n"] += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(service, "analyze_folder", spy)
    result = service.process_upload_batch_analyze(batch_id, tenant_id="idem")
    assert result["run_id"] == run_id
    assert result.get("already_analyzed") is True
    assert calls["n"] == 0
    batch = db.get_upload_batch(batch_id)
    assert batch["status"] == "analyzed"