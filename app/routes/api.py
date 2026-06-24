from __future__ import annotations

import logging
import secrets
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from .. import (
    audit,
    config,
    homelab,
    metrics,
    service,
    uploads,
)
from ..async_io import run_sync
from ..auth import require_bearer, token_from_request, verify_api_access
from ..auth_context import AuthContext
from ..metering import MeteringError

log = logging.getLogger("plutus")
router = APIRouter()

def _mise_recommend_uses_hook_token(request: Request) -> bool:
    """Flow Mise posts the dedicated hook secret to /recommend/mise-gallery."""
    expected = config.MISE_HOOK_TOKEN
    if not (config.SAAS_MODE and expected):
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
    tenant_id: str | None = Form(None),
) -> JSONResponse:
    from .. import mise_hook

    if _mise_recommend_uses_hook_token(request):
        scope = mise_hook.resolve_hook_tenant_id(tenant_id, from_webhook=True)
        result = await run_sync(
            mise_hook.recommend_published_gallery,
            mise_gallery_id=mise_gallery_id,
            tenant_id=scope,
            argus_run_id=argus_run_id,
            limit=limit,
            from_webhook=True,
        )
        if scope:
            metrics.inc_tenant(scope, "recommend_mise")
            audit.record(
                "recommend.mise.hook",
                request=request,
                tenant_id=scope,
                resource=str(result["run_id"]),
                detail={"mise_gallery_id": mise_gallery_id, "via": "recommend"},
            )
        return JSONResponse(result)

    ctx = verify_api_access(
        request, authorization=request.headers.get("Authorization")
    )
    if config.SAAS_MODE and ctx.is_admin:
        scope = mise_hook.resolve_hook_tenant_id(tenant_id)
    elif config.SAAS_MODE:
        scope = ctx.tenant_id
    elif homelab.store_enabled():
        homelab.ensure_bootstrap()
        scope = homelab.tenant_id()
    else:
        scope = None
    result = await run_sync(
        mise_hook.recommend_published_gallery,
        mise_gallery_id=mise_gallery_id,
        tenant_id=scope,
        argus_run_id=argus_run_id,
        limit=limit,
    )
    if scope:
        metrics.inc_tenant(scope, "recommend_mise")
        audit.record("recommend.mise", request=request, ctx=ctx, resource=str(result["run_id"]))
    return JSONResponse(result)



@router.post("/recommend/upload-batch")
async def recommend_upload_batch_api(
    request: Request,
    batch_id: str = Form(...),
    name: str | None = Form(None),
    argus_run_id: int | None = Form(None),
    limit: int | None = Form(None),
    sync: str | None = Form(None),
    ctx: AuthContext = Depends(require_bearer),
) -> JSONResponse:
    if not config.SAAS_MODE or not ctx.tenant_id:
        raise HTTPException(status_code=403, detail="tenant API key required")
    async_mode = False if sync else None
    try:
        result = await run_sync(
            service.analyze_upload_batch,
            batch_id,
            tenant_id=ctx.tenant_id,
            name=name,
            argus_run_id=argus_run_id,
            limit=limit,
            async_mode=async_mode,
        )
    except MeteringError as exc:
        raise HTTPException(status_code=402, detail=str(exc)) from exc
    except (service.RecommendError, uploads.UploadError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if result.get("queued"):
        return JSONResponse(result, status_code=202)
    metrics.inc_tenant(ctx.tenant_id, "recommend_upload")
    audit.record(
        "recommend.upload",
        request=request,
        ctx=ctx,
        resource=str(result.get("run_id")),
        detail={"batch_id": batch_id},
    )
    return JSONResponse(result)



@router.get("/upload-batches/{batch_id}/status", response_class=JSONResponse)
def upload_batch_status_api(batch_id: str, ctx: AuthContext = Depends(require_bearer)):
    if not config.SAAS_MODE or not ctx.tenant_id:
        raise HTTPException(status_code=403, detail="tenant API key required")
    try:
        return service.upload_batch_status(batch_id, tenant_id=ctx.tenant_id)
    except service.RecommendError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc



@router.post("/analyze-folder")
async def analyze_folder_api(
    folder: str = Form(...),
    name: str | None = Form(None),
    argus_run_id: int | None = Form(None),
    limit: int | None = Form(None),
    ctx: AuthContext = Depends(require_bearer),
) -> JSONResponse:
    if config.SAAS_MODE and not ctx.is_admin:
        raise HTTPException(status_code=403, detail="folder analyze requires admin in SaaS mode")
    path = Path(folder).expanduser()
    try:
        result = await run_sync(
            service.analyze_folder,
            path,
            name=name,
            argus_run_id=argus_run_id,
            limit=limit,
        )
    except MeteringError as exc:
        raise HTTPException(status_code=402, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    metrics.inc("recommend_folder")
    return JSONResponse(result)





@router.get("/api/mise/galleries", response_class=JSONResponse)
def api_mise_galleries(
    published: bool | None = Query(None),
    ctx: AuthContext = Depends(require_bearer),
) -> JSONResponse:
    from .. import mise_client

    if not mise_client.is_enabled():
        raise HTTPException(status_code=503, detail="Mise API is not configured")
    try:
        body = mise_client.list_galleries(published=published)
    except mise_client.MiseClientError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return JSONResponse(body)


