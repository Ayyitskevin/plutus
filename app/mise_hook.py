"""Mise gallery recommend — studio admin feature (no SaaS tenant scope)."""
from __future__ import annotations

from fastapi import HTTPException

from . import service


def recommend_published_gallery(
    *,
    mise_gallery_id: int,
    argus_run_id: int | None = None,
    limit: int | None = None,
    correlation_id: str | None = None,
) -> dict:
    try:
        return service.analyze_mise_gallery(
            mise_gallery_id,
            limit=limit,
            argus_run_id=argus_run_id,
            correlation_id=correlation_id,
        )
    except service.RecommendError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc