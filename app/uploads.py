"""Tenant gallery upload batches for SaaS mode."""
from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

from fastapi import UploadFile

from . import config, db, storage

_UPLOAD_CHUNK_BYTES = 1024 * 1024


class UploadError(Exception):
    """Upload validation or batch failure."""


def create_batch(*, tenant_id: str, name: str) -> dict:
    batch_id = uuid.uuid4().hex
    return db.create_upload_batch(batch_id=batch_id, tenant_id=tenant_id, name=name.strip())


def get_batch(batch_id: str, *, tenant_id: str | None = None) -> dict | None:
    return db.get_upload_batch(batch_id, tenant_id=tenant_id)


async def _upload_chunks(upload: UploadFile) -> AsyncIterator[bytes]:
    while True:
        chunk = await upload.read(_UPLOAD_CHUNK_BYTES)
        if not chunk:
            break
        yield chunk


def _supported_upload(upload: UploadFile) -> str | None:
    if not upload.filename:
        return None
    ext = "." + upload.filename.rsplit(".", 1)[-1].lower() if "." in upload.filename else ""
    if ext not in config.PHOTO_EXTS:
        return None
    return upload.filename


async def add_upload_files(
    *,
    tenant_id: str,
    batch_id: str,
    files: list[UploadFile],
) -> dict:
    """Stream multipart uploads to storage without buffering whole files in memory."""
    batch = db.get_upload_batch(batch_id, tenant_id=tenant_id)
    if not batch:
        raise UploadError("upload batch not found")
    if batch["status"] == "analyzed":
        raise UploadError("batch already analyzed — start a new upload")

    candidates = [f for f in files if _supported_upload(f)]
    if not candidates:
        raise UploadError("no supported image files (jpg, png, webp, heic, tif)")
    if len(candidates) > config.MAX_UPLOAD_FILES:
        raise UploadError(f"maximum {config.MAX_UPLOAD_FILES} files per batch")

    saved = 0
    for upload in candidates:
        filename = _supported_upload(upload)
        assert filename is not None
        try:
            await storage.save_gallery_stream(
                tenant_id,
                batch_id,
                filename,
                _upload_chunks(upload),
                max_bytes=config.MAX_UPLOAD_FILE_BYTES,
            )
        except storage.StorageError as exc:
            raise UploadError(str(exc)) from exc
        saved += 1

    if saved == 0:
        raise UploadError("no supported image files (jpg, png, webp, heic, tif)")

    uris = storage.list_gallery_uris(tenant_id, batch_id)
    db.update_upload_batch(
        batch_id,
        photo_count=len(uris),
        status="ready",
    )
    return db.get_upload_batch(batch_id, tenant_id=tenant_id) or {}


def add_files(
    *,
    tenant_id: str,
    batch_id: str,
    files: list[tuple[str, bytes]],
) -> dict:
    batch = db.get_upload_batch(batch_id, tenant_id=tenant_id)
    if not batch:
        raise UploadError("upload batch not found")
    if batch["status"] == "analyzed":
        raise UploadError("batch already analyzed — start a new upload")

    if not files:
        raise UploadError("no files provided")
    if len(files) > config.MAX_UPLOAD_FILES:
        raise UploadError(f"maximum {config.MAX_UPLOAD_FILES} files per batch")

    saved = 0
    for filename, data in files:
        if len(data) > config.MAX_UPLOAD_FILE_BYTES:
            raise UploadError(
                f"{filename} exceeds {config.MAX_UPLOAD_FILE_BYTES // (1024 * 1024)}MB limit"
            )
        ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        if ext not in config.PHOTO_EXTS:
            continue
        try:
            storage.save_gallery_file(tenant_id, batch_id, filename, data)
        except storage.StorageError as exc:
            raise UploadError(str(exc)) from exc
        saved += 1

    if saved == 0:
        raise UploadError("no supported image files (jpg, png, webp, heic, tif)")

    uris = storage.list_gallery_uris(tenant_id, batch_id)
    db.update_upload_batch(
        batch_id,
        photo_count=len(uris),
        status="ready",
    )
    return db.get_upload_batch(batch_id, tenant_id=tenant_id) or {}


def batch_folder(tenant_id: str, batch_id: str) -> Any:
    batch = db.get_upload_batch(batch_id, tenant_id=tenant_id)
    if not batch:
        raise UploadError("upload batch not found")
    if batch["photo_count"] <= 0:
        raise UploadError("batch has no photos — upload files first")
    return storage.prepare_gallery_folder(tenant_id, batch_id)