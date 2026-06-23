"""Orchestration — ingest, recommend, persist."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from . import db, ingest, recommend


def analyze_folder(
    folder: Path,
    *,
    name: str | None = None,
    argus_run_id: int | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    db.migrate()
    photos = ingest.photos_from_folder(folder, limit=limit)
    if argus_run_id:
        photos = ingest.enrich_from_argus_run(photos, argus_run_id)

    payload = recommend.recommend_bundles(photos)
    gallery_id = db.insert_gallery(
        name=name or folder.name,
        source=str(folder),
        photo_count=len(photos),
    )
    run_id = db.insert_run(
        gallery_id=gallery_id,
        engine=payload.get("engine", "mock"),
        bundle_count=len(payload.get("bundles") or []),
        estimated_total_cents=int(payload.get("estimated_total_cents") or 0),
        payload=payload,
    )
    return {
        "run_id": run_id,
        "gallery_id": gallery_id,
        **payload,
    }