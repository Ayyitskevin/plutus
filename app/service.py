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
) -> dict[str, Any]:
    bundle_count = len(payload.get("bundles") or [])
    total_cents = int(payload.get("estimated_total_cents") or 0)
    engine = payload.get("engine", "mock")

    existing = (
        db.get_gallery_by_mise_id(mise_gallery_id)
        if mise_gallery_id is not None
        else None
    )
    if existing:
        # Idempotency invariant: one stable offer per Mise gallery. Reuse the same
        # gallery + run rows so a re-run refreshes the offer in place rather than
        # creating a second offer or duplicate bundles. run_id stays stable.
        gallery_id = int(existing["id"])
        db.update_gallery(gallery_id, name=name, photo_count=len(photos))
        run_id = db.run_id_for_gallery(gallery_id)
        if run_id is not None:
            db.update_run(
                run_id,
                bundle_count=bundle_count,
                estimated_total_cents=total_cents,
                payload=payload,
            )
        else:
            run_id = db.insert_run(
                gallery_id=gallery_id,
                engine=engine,
                bundle_count=bundle_count,
                estimated_total_cents=total_cents,
                payload=payload,
            )
    else:
        gallery_id = db.insert_gallery(
            name=name,
            source=source,
            photo_count=len(photos),
            mise_gallery_id=mise_gallery_id,
        )
        run_id = db.insert_run(
            gallery_id=gallery_id,
            engine=engine,
            bundle_count=bundle_count,
            estimated_total_cents=total_cents,
            payload=payload,
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
) -> dict[str, Any]:
    db.migrate()
    photos = ingest.photos_from_folder(folder, limit=limit)
    if argus_run_id:
        photos = ingest.enrich_from_argus_run(photos, argus_run_id)

    payload = recommend.recommend_bundles(photos)
    return _persist_run(
        name=name or folder.name,
        source=str(folder),
        photos=photos,
        payload=payload,
        mise_gallery_id=mise_gallery_id,
    )


def analyze_mise_gallery(
    mise_gallery_id: int,
    *,
    limit: int | None = None,
    argus_run_id: int | None = None,
    correlation_id: str | None = None,
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
    )
    result["mise_gallery_id"] = mise_gallery_id
    result["argus_run_id"] = effective_argus
    run_id = int(result["run_id"])
    result.update(studio_run_urls(run_id))
    result["bundle_count"] = len(result.get("bundles") or [])
    result["estimated_total_cents"] = int(result.get("estimated_total_cents") or 0)
    # Echo Mise's correlation id unchanged when provided (request metadata, not part
    # of the persisted offer, so the stored run stays idempotent across retries).
    if correlation_id:
        result["correlation_id"] = correlation_id
    return result


def studio_run_urls(run_id: int) -> dict[str, str]:
    """Links for Mise admin — bundle review UI and copy-paste client pitch.

    `offer_url` is the contract name; `review_url` is kept as a backward-compatible
    alias for the current Mise consumer.
    """
    base = config.PUBLIC_URL.rstrip("/")
    return {
        "review_url": f"{base}/runs/{run_id}",
        "offer_url": f"{base}/runs/{run_id}",
        "pitch_url": f"{base}/runs/{run_id}/pitch.txt",
    }
