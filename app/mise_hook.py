"""Mise gallery recommend — studio admin feature (no SaaS tenant scope)."""
from __future__ import annotations

from fastapi import HTTPException

from . import service
from .metering import MeteringError


def recommend_published_gallery(
    *,
    mise_gallery_id: int,
    tenant_id: str | None = None,
    argus_run_id: int | None = None,
    limit: int | None = None,
    from_webhook: bool = False,
) -> dict:
    del tenant_id, from_webhook  # studio mode: single operator, no tenant routing
    try:
        return service.analyze_mise_gallery(
            mise_gallery_id,
            limit=limit,
            argus_run_id=argus_run_id,
            tenant_id=None,
        )
    except MeteringError as exc:
        raise HTTPException(status_code=402, detail=str(exc)) from exc
    except service.RecommendError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc