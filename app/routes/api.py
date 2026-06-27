from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from .. import metrics, mise_callback, mise_client, offer_schema, service, service_tokens
from ..async_io import run_sync
from ..auth import require_bearer, token_from_request
from ..auth_context import AuthContext

log = logging.getLogger("plutus")
router = APIRouter()


@router.post("/recommend/mise-gallery")
async def recommend_mise_gallery_api(
    request: Request,
    mise_gallery_id: int = Form(...),
    limit: int | None = Form(None),
    argus_run_id: int | None = Form(None),
    correlation_id: str | None = Form(None),
) -> JSONResponse:
    from .. import mise_hook

    # Single constant-time service-token register: PLUTUS_API_TOKEN,
    # PLUTUS_MISE_HOOK_TOKEN, and any PLUTUS_SERVICE_TOKENS rotation tokens are all
    # accepted. Open only when no token is configured (studio-dev default).
    if service_tokens.auth_required():
        provided = token_from_request(
            request, authorization=request.headers.get("Authorization")
        )
        if not service_tokens.verify(provided):
            raise HTTPException(status_code=401, detail="invalid or missing service token")
    result = await run_sync(
        mise_hook.recommend_published_gallery,
        mise_gallery_id=mise_gallery_id,
        argus_run_id=argus_run_id,
        limit=limit,
        correlation_id=correlation_id,
    )
    metrics.inc("recommend_mise")
    # Optional async push back to Mise (default OFF — the synchronous response
    # above stays the live contract). deliver() never raises (retry/refresh/
    # dead-letter handled internally), so a callback failure can't crash recommend.
    if mise_callback.callback_enabled():
        result["callback"] = await run_sync(
            mise_callback.deliver,
            gallery_id=mise_gallery_id,
            run_id=result.get("run_id"),
            payload=offer_schema.to_mise_offer(result),
            correlation_id=correlation_id,
        )
    return JSONResponse(result)


@router.post("/admin/callbacks/redeliver")
async def redeliver_callbacks_api(
    ctx: AuthContext = Depends(require_bearer),
) -> JSONResponse:
    """Flush the callback dead-letter outbox (re-attempt persisted failures)."""
    return JSONResponse(await run_sync(mise_callback.redeliver_pending))


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