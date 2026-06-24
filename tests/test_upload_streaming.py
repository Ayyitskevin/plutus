"""Streaming multipart uploads — chunked storage without full in-memory buffer."""
from __future__ import annotations

import asyncio
import io
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app import config, db, storage, uploads


class _ChunkedUpload:
    """Minimal UploadFile stand-in for unit tests."""

    def __init__(self, filename: str, chunks: list[bytes]) -> None:
        self.filename = filename
        self._chunks = list(chunks)
        self._index = 0

    async def read(self, size: int = -1) -> bytes:
        del size
        if self._index >= len(self._chunks):
            return b""
        chunk = self._chunks[self._index]
        self._index += 1
        return chunk


def _tiny_jpeg() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (12, 10), color=(20, 40, 60)).save(buf, format="JPEG")
    return buf.getvalue()


@pytest.fixture()
def upload_env(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(config, "STORAGE_BACKEND", "local")
    db.migrate()
    from app import tenants

    tenants.create_tenant("stream", name="Stream Co", store_slug="stream")
    batch = uploads.create_batch(tenant_id="stream", name="Chunks")
    return batch["id"]


def test_add_upload_files_streams_chunks(upload_env):
    data = _tiny_jpeg()
    half = max(1, len(data) // 2)
    upload = _ChunkedUpload("photo.jpg", [data[:half], data[half:]])

    async def _run() -> dict:
        return await uploads.add_upload_files(
            tenant_id="stream",
            batch_id=upload_env,
            files=[upload],
        )

    result = asyncio.run(_run())
    assert result["photo_count"] == 1
    uris = storage.list_gallery_uris("stream", upload_env)
    assert len(uris) == 1
    assert Path(uris[0]).read_bytes() == data


def test_add_upload_files_rejects_oversized_mid_stream(upload_env, monkeypatch):
    monkeypatch.setattr(config, "MAX_UPLOAD_FILE_BYTES", 10)
    upload = _ChunkedUpload("big.jpg", [b"x" * 6, b"y" * 6])

    async def _run() -> None:
        await uploads.add_upload_files(
            tenant_id="stream",
            batch_id=upload_env,
            files=[upload],
        )

    with pytest.raises(uploads.UploadError, match="exceeds"):
        asyncio.run(_run())
    assert storage.list_gallery_uris("stream", upload_env) == []


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


def test_s3_upload_uses_multipart_transfer_config(monkeypatch):
    from unittest.mock import MagicMock, patch

    captured: dict = {}

    class _FakeTransferConfig:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    with patch("boto3.s3.transfer.TransferConfig", _FakeTransferConfig):
        with patch("app.storage._s3_client") as mock_client:
            mock_client.return_value = MagicMock()
            monkeypatch.setattr(config, "STORAGE_BACKEND", "s3")
            monkeypatch.setattr(config, "S3_BUCKET", "test-bucket")
            monkeypatch.setattr(config, "S3_ACCESS_KEY", "key")
            monkeypatch.setattr(config, "S3_SECRET_KEY", "secret")
            storage.save_gallery_file("t", "b", "photo.jpg", b"x" * 100)
    assert captured.get("multipart_threshold") == 8 * 1024 * 1024
    assert captured.get("multipart_chunksize") == 8 * 1024 * 1024
    mock_client.return_value.upload_fileobj.assert_called_once()


def test_ui_upload_rejects_oversized_streaming(saas_client, monkeypatch):
    from app import tenants

    monkeypatch.setattr(config, "MAX_UPLOAD_FILE_BYTES", 50)
    tenants.create_tenant("lim", name="Limit Co", store_slug="lim")
    issued = tenants.issue_api_key("lim")
    saas_client.post(
        "/ui/saas/login",
        data={"api_token": issued["api_key"]},
        follow_redirects=False,
    )
    big = _tiny_jpeg() * 3
    r = saas_client.post(
        "/ui/saas/app/upload",
        data={"gallery_name": "Too big"},
        files=[("files", ("big.jpg", big, "image/jpeg"))],
        follow_redirects=False,
    )
    assert r.status_code == 400
    assert b"exceeds" in r.content.lower()