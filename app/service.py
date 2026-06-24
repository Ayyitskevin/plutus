"""Orchestration — ingest, recommend, persist."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from . import config, db, ingest, mise_client, recommend

log = logging.getLogger("plutus.service")


class RecommendError(Exception):
    """Human-readable failure for API responses."""


def _resolve_folder(*, mise_gallery_id: int, originals_path: str | None) -> Path:
    if config.MISE_MEDIA_ROOT:
        local = config.MISE_MEDIA_ROOT / str(mise_gallery_id) / "original"
        if local.is_dir():
            return local
    if originals_path:
        remote = Path(originals_path)
        if remote.is_dir():
            return remote
    raise RecommendError(
        f"gallery {mise_gallery_id} originals not found locally "
        "(sync with scripts/sync-mise-media.sh or set PLUTUS_MISE_MEDIA_ROOT)"
    )


def _persist_run(
    *,
    name: str,
    source: str,
    photos: list[dict[str, Any]],
    payload: dict[str, Any],
    mise_gallery_id: int | None = None,
    tenant_id: str | None = None,
) -> dict[str, Any]:
    gallery_id = db.insert_gallery(
        name=name,
        source=source,
        photo_count=len(photos),
        mise_gallery_id=mise_gallery_id,
        tenant_id=tenant_id,
    )
    run_id = db.insert_run(
        gallery_id=gallery_id,
        engine=payload.get("engine", "mock"),
        bundle_count=len(payload.get("bundles") or []),
        estimated_total_cents=int(payload.get("estimated_total_cents") or 0),
        payload=payload,
        tenant_id=tenant_id,
    )
    return {
        "run_id": run_id,
        "gallery_id": gallery_id,
        **payload,
    }


def analyze_folder(
    folder: Path,
    *,
    name: str | None = None,
    argus_run_id: int | None = None,
    limit: int | None = None,
    mise_gallery_id: int | None = None,
    tenant_id: str | None = None,
) -> dict[str, Any]:
    db.migrate()
    if tenant_id:
        from .metering import check_recommend_cap, record_recommend

        check_recommend_cap(tenant_id)
    photos = ingest.photos_from_folder(folder, limit=limit)
    if argus_run_id:
        photos = ingest.enrich_from_argus_run(photos, argus_run_id)

    payload = recommend.recommend_bundles(photos, tenant_id=tenant_id)
    result = _persist_run(
        name=name or folder.name,
        source=str(folder),
        photos=photos,
        payload=payload,
        mise_gallery_id=mise_gallery_id,
        tenant_id=tenant_id,
    )
    if tenant_id:
        from .metering import record_recommend

        record_recommend(tenant_id)
    return result


def analyze_mise_gallery(
    mise_gallery_id: int,
    *,
    limit: int | None = None,
    argus_run_id: int | None = None,
    tenant_id: str | None = None,
) -> dict[str, Any]:
    db.migrate()
    if not mise_client.is_enabled():
        raise RecommendError("Mise API is not configured")

    row = mise_client.get_gallery(mise_gallery_id)
    if not row:
        raise RecommendError(f"mise gallery {mise_gallery_id} not found")
    if not row.get("published"):
        raise RecommendError(f"mise gallery {mise_gallery_id} is not published")

    folder = _resolve_folder(
        mise_gallery_id=mise_gallery_id,
        originals_path=row.get("originals_path"),
    )
    effective_argus = argus_run_id or row.get("argus_last_run_id")
    result = analyze_folder(
        folder,
        name=row.get("title") or f"Gallery {mise_gallery_id}",
        argus_run_id=int(effective_argus) if effective_argus else None,
        limit=limit,
        mise_gallery_id=mise_gallery_id,
        tenant_id=tenant_id,
    )
    result["mise_gallery_id"] = mise_gallery_id
    result["argus_run_id"] = effective_argus
    if config.MISE_AUTO_OFFER and tenant_id:
        from . import sell

        try:
            offer = sell.publish_offer(
                tenant_id,
                int(result["run_id"]),
                label=row.get("title") or f"Gallery {mise_gallery_id}",
            )
            result.update(
                {
                    k: offer[k]
                    for k in ("offer_url", "offer_token", "store_slug")
                    if k in offer
                }
            )
        except sell.SellError as exc:
            log.warning(
                "mise auto-offer skipped for gallery %s run %s: %s",
                mise_gallery_id,
                result.get("run_id"),
                exc,
            )
    return result


def _resolve_argus_run_id(
    folder: Path,
    *,
    tenant_id: str,
    argus_run_id: int | None,
    limit: int | None,
) -> int | None:
    if argus_run_id:
        return argus_run_id
    from . import argus_client

    if not config.ARGUS_AUTO_VISION or not argus_client.is_enabled():
        return None
    try:
        return argus_client.analyze_folder(
            folder,
            limit=limit,
            client_id=f"plutus:{tenant_id}",
        )
    except argus_client.ArgusClientError as exc:
        raise RecommendError(f"Argus vision failed: {exc}") from exc


def enqueue_upload_batch_analyze(
    batch_id: str,
    *,
    tenant_id: str,
    argus_run_id: int | None = None,
) -> dict[str, Any]:
    from . import uploads

    batch = uploads.get_batch(batch_id, tenant_id=tenant_id)
    if not batch:
        raise RecommendError("upload batch not found")
    if batch["status"] in {"queued", "analyzing"}:
        return {"batch_id": batch_id, "status": batch["status"], "queued": True}
    if batch["status"] == "analyzed":
        raise RecommendError("batch already analyzed — start a new upload")
    if batch["photo_count"] <= 0:
        raise uploads.UploadError("batch has no photos — upload files first")

    db.update_upload_batch(
        batch_id,
        status="queued",
        analyze_error=None,
        argus_run_id=argus_run_id,
    )
    return {"batch_id": batch_id, "status": "queued", "queued": True}


def process_upload_batch_analyze(
    batch_id: str,
    *,
    tenant_id: str,
    name: str | None = None,
    argus_run_id: int | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    from . import uploads

    batch = uploads.get_batch(batch_id, tenant_id=tenant_id)
    if not batch:
        raise RecommendError("upload batch not found")
    existing_run_id = batch.get("run_id")
    if existing_run_id:
        db.update_upload_batch(batch_id, status="analyzed", analyze_error=None)
        return {
            "run_id": int(existing_run_id),
            "upload_batch_id": batch_id,
            "argus_run_id": batch.get("argus_run_id"),
            "already_analyzed": True,
        }
    folder = uploads.batch_folder(tenant_id, batch_id)
    effective_argus = _resolve_argus_run_id(
        folder,
        tenant_id=tenant_id,
        argus_run_id=argus_run_id,
        limit=limit,
    )
    result = analyze_folder(
        folder,
        name=name or batch["name"],
        argus_run_id=effective_argus,
        limit=limit,
        tenant_id=tenant_id,
    )
    db.update_upload_batch(
        batch_id,
        status="analyzed",
        run_id=result["run_id"],
        argus_run_id=effective_argus,
        analyze_error=None,
    )
    result["upload_batch_id"] = batch_id
    result["argus_run_id"] = effective_argus
    return result


def analyze_upload_batch(
    batch_id: str,
    *,
    tenant_id: str,
    name: str | None = None,
    argus_run_id: int | None = None,
    limit: int | None = None,
    async_mode: bool | None = None,
) -> dict[str, Any]:
    use_async = config.UPLOAD_ASYNC_ANALYZE if async_mode is None else async_mode
    if use_async and config.SAAS_MODE:
        return enqueue_upload_batch_analyze(
            batch_id,
            tenant_id=tenant_id,
            argus_run_id=argus_run_id,
        )
    return process_upload_batch_analyze(
        batch_id,
        tenant_id=tenant_id,
        name=name,
        argus_run_id=argus_run_id,
        limit=limit,
    )


def upload_batch_status(batch_id: str, *, tenant_id: str) -> dict[str, Any]:
    from . import uploads

    batch = uploads.get_batch(batch_id, tenant_id=tenant_id)
    if not batch:
        raise RecommendError("upload batch not found")
    out: dict[str, Any] = {
        "batch_id": batch_id,
        "status": batch["status"],
        "photo_count": batch["photo_count"],
        "run_id": batch.get("run_id"),
        "argus_run_id": batch.get("argus_run_id"),
        "analyze_error": batch.get("analyze_error"),
        "done": batch["status"] == "analyzed",
        "failed": batch["status"] == "failed",
        "pending": batch["status"] in {"queued", "analyzing"},
    }
    if batch.get("run_id"):
        out["run_url"] = f"/runs/{batch['run_id']}"
    return out