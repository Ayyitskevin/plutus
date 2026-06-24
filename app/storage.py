"""Tenant gallery storage — local disk (default) or S3-compatible."""
from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from pathlib import Path

from . import config

_UPLOAD_CHUNK_BYTES = 1024 * 1024
_S3_MULTIPART_THRESHOLD = 8 * 1024 * 1024
_S3_MULTIPART_CHUNK = 8 * 1024 * 1024

log = logging.getLogger("plutus.storage")


class StorageError(Exception):
    """Raised when a storage operation fails."""


def _s3_ready() -> bool:
    return bool(
        config.STORAGE_BACKEND == "s3"
        and config.S3_BUCKET
        and config.S3_ACCESS_KEY
        and config.S3_SECRET_KEY
    )


def gallery_prefix(tenant_id: str, batch_id: str) -> str:
    return f"{config.S3_PREFIX.rstrip('/')}/{tenant_id}/galleries/{batch_id}/original"


def _local_originals_dir(tenant_id: str, batch_id: str) -> Path:
    root = config.DATA_DIR / "tenants" / tenant_id / "galleries" / batch_id / "original"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _cache_dir(tenant_id: str, batch_id: str) -> Path:
    root = config.DATA_DIR / "s3_cache" / tenant_id / batch_id / "original"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _s3_client():
    try:
        import boto3
        from botocore.config import Config as BotoConfig
    except ImportError as exc:
        raise StorageError("boto3 required for PLUTUS_STORAGE_BACKEND=s3") from exc
    kwargs: dict = {
        "service_name": "s3",
        "region_name": config.S3_REGION,
        "aws_access_key_id": config.S3_ACCESS_KEY,
        "aws_secret_access_key": config.S3_SECRET_KEY,
        "config": BotoConfig(signature_version="s3v4"),
    }
    if config.S3_ENDPOINT:
        kwargs["endpoint_url"] = config.S3_ENDPOINT
    return boto3.client(**kwargs)


def _s3_upload_fileobj(client, body, bucket: str, key: str) -> None:
    from boto3.s3.transfer import TransferConfig

    transfer = TransferConfig(
        multipart_threshold=_S3_MULTIPART_THRESHOLD,
        multipart_chunksize=_S3_MULTIPART_CHUNK,
        max_concurrency=4,
        use_threads=True,
    )
    client.upload_fileobj(body, bucket, key, Config=transfer)


def _safe_gallery_name(filename: str) -> str:
    return Path(filename or "photo.jpg").name


def _unique_dest_path(folder: Path, safe: str) -> Path:
    dest = folder / safe
    if not dest.exists():
        return dest
    stem = Path(safe).stem
    suffix = Path(safe).suffix
    n = 1
    while True:
        candidate = folder / f"{stem}-{n}{suffix}"
        if not candidate.exists():
            return candidate
        n += 1


async def save_gallery_stream(
    tenant_id: str,
    batch_id: str,
    filename: str,
    chunks: AsyncIterator[bytes],
    *,
    max_bytes: int,
) -> str:
    """Stream gallery bytes to storage without buffering the full file in memory."""
    safe = _safe_gallery_name(filename)
    total = 0
    staged: Path | None = None
    dest: Path | None = None

    try:
        if config.STORAGE_BACKEND == "s3" and _s3_ready():
            cache = _cache_dir(tenant_id, batch_id)
            staged = _unique_dest_path(cache, safe)
            with staged.open("wb") as out:
                async for chunk in chunks:
                    total += len(chunk)
                    if total > max_bytes:
                        raise StorageError(
                            f"{safe} exceeds {max_bytes // (1024 * 1024)}MB limit"
                        )
                    out.write(chunk)
            key = f"{gallery_prefix(tenant_id, batch_id)}/{staged.name}"
            client = _s3_client()
            with staged.open("rb") as body:
                _s3_upload_fileobj(client, body, config.S3_BUCKET, key)
            staged.unlink(missing_ok=True)
            staged = None
            uri = f"s3://{config.S3_BUCKET}/{key}"
            log.info("stored gallery file %s", uri)
            return uri

        folder = _local_originals_dir(tenant_id, batch_id)
        dest = _unique_dest_path(folder, safe)
        with dest.open("wb") as out:
            async for chunk in chunks:
                total += len(chunk)
                if total > max_bytes:
                    raise StorageError(
                        f"{safe} exceeds {max_bytes // (1024 * 1024)}MB limit"
                    )
                out.write(chunk)
        return str(dest.resolve())
    except Exception:
        for path in (staged, dest):
            if path is not None and path.exists():
                path.unlink(missing_ok=True)
        raise


def save_gallery_file(
    tenant_id: str,
    batch_id: str,
    filename: str,
    data: bytes,
) -> str:
    """Persist one gallery original; returns URI (path or s3://)."""
    safe = _safe_gallery_name(filename)
    if config.STORAGE_BACKEND == "s3" and _s3_ready():
        import io

        key = f"{gallery_prefix(tenant_id, batch_id)}/{safe}"
        client = _s3_client()
        _s3_upload_fileobj(client, io.BytesIO(data), config.S3_BUCKET, key)
        uri = f"s3://{config.S3_BUCKET}/{key}"
        log.info("stored gallery file %s", uri)
        return uri
    folder = _local_originals_dir(tenant_id, batch_id)
    dest = _unique_dest_path(folder, safe)
    dest.write_bytes(data)
    return str(dest.resolve())


def list_gallery_uris(tenant_id: str, batch_id: str) -> list[str]:
    if config.STORAGE_BACKEND == "s3" and _s3_ready():
        prefix = gallery_prefix(tenant_id, batch_id) + "/"
        client = _s3_client()
        uris: list[str] = []
        token = None
        while True:
            kwargs: dict = {"Bucket": config.S3_BUCKET, "Prefix": prefix}
            if token:
                kwargs["ContinuationToken"] = token
            resp = client.list_objects_v2(**kwargs)
            for row in resp.get("Contents") or []:
                key = row["Key"]
                if key.endswith("/"):
                    continue
                uris.append(f"s3://{config.S3_BUCKET}/{key}")
            if not resp.get("IsTruncated"):
                break
            token = resp.get("NextContinuationToken")
        return sorted(uris)
    folder = _local_originals_dir(tenant_id, batch_id)
    if not folder.is_dir():
        return []
    return sorted(
        str(p.resolve())
        for p in folder.iterdir()
        if p.is_file() and p.suffix.lower() in config.PHOTO_EXTS
    )


def _materialize_s3_uri(uri: str, cache_dir: Path) -> Path:
    without = uri.removeprefix("s3://")
    bucket, _, key = without.partition("/")
    name = Path(key).name
    cache_path = cache_dir / name
    if cache_path.exists():
        return cache_path
    client = _s3_client()
    obj = client.get_object(Bucket=bucket, Key=key)
    cache_path.write_bytes(obj["Body"].read())
    return cache_path


def prepare_gallery_folder(tenant_id: str, batch_id: str) -> Path:
    """Return a local directory containing all batch originals for ingest."""
    if config.STORAGE_BACKEND == "s3" and _s3_ready():
        cache = _cache_dir(tenant_id, batch_id)
        for uri in list_gallery_uris(tenant_id, batch_id):
            _materialize_s3_uri(uri, cache)
        return cache
    return _local_originals_dir(tenant_id, batch_id)


def storage_status() -> dict:
    backend = config.STORAGE_BACKEND
    if backend == "s3":
        ready = _s3_ready()
        return {"backend": "s3", "configured": ready, "bucket": config.S3_BUCKET}
    return {"backend": "local", "configured": True}