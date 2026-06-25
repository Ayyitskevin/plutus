from __future__ import annotations

import logging
import secrets
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from .. import config, metrics, mise_client, service
from ..async_io import run_sync
from ..auth import require_bearer, token_from_request, verify_api_access
from ..auth_context import AuthContext

log = logging.getLogger("plutus")
router = APIRouter()


def _mise_recommend_uses_hook_token(request: Request) -> bool:
    """Flow Mise syncs PLUTUS_MISE_HOOK_TOKEN as MISE_PLUTUS_TOKEN on publish."""
    expected = config.MISE_HOOK_TOKEN
    if not expected:
        return False
    provided = token_from_request(
        request, authorization=request.headers.get("Authorization")
    )
    return bool(provided and secrets.compare_digest(provided, expected))


@router.post("/recommend/mise-gallery")
async def recommend_mise_gallery_api(
    request: Request,
    mise_gallery_id: int = Form(...),
    limit: int | None = Form(None),
    argus_run_id: int | None = Form(None),
) -> JSONResponse:
    from .. import mise_hook

    if not _mise_recommend_uses_hook_token(request):
        verify_api_access(request, authorization=request.headers.get("Authorization"))
    result = await run_sync(
        mise_hook.recommend_published_gallery,
        mise_gallery_id=mise_gallery_id,
        argus_run_id=argus_run_id,
        limit=limit,
    )
    metrics.inc("recommend_mise")
    return JSONResponse(result)


@router.post("/analyze-folder")
async def analyze_folder_api(
    folder: str = Form(...),
    name: str | None = Form(None),
    argus_run_id: int | None = Form(None),
    limit: int | None = Form(None),
    ctx: AuthContext = Depends(require_bearer),
) -> JSONResponse:
    path = Path(folder).expanduser()
    try:
        result = await run_sync(
            service.analyze_folder,
            path,
            name=name,
            argus_run_id=argus_run_id,
            limit=limit,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    metrics.inc("recommend_folder")
    return JSONResponse(result)


@router.get("/api/mise/galleries", response_class=JSONResponse)
def api_mise_galleries(
    published: bool | None = Query(None),
    ctx: AuthContext = Depends(require_bearer),
) -> JSONResponse:
    if not mise_client.is_enabled():
        raise HTTPException(status_code=503, detail="Mise API is not configured")
    try:
        body = mise_client.list_galleries(published=published)
    except mise_client.MiseClientError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return JSONResponse(body)