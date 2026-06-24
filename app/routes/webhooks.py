from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Form, Request
from fastapi.responses import JSONResponse

from .. import (
    audit,
    billing,
    config,
    metrics,
)
from .deps import (
    error,
)

log = logging.getLogger("plutus")
router = APIRouter()

@router.post("/webhooks/mise/gallery-published")
def mise_gallery_published_webhook(
    request: Request,
    mise_gallery_id: int = Form(...),
    tenant_id: str | None = Form(None),
    argus_run_id: int | None = Form(None),
    limit: int | None = Form(None),
) -> JSONResponse:
    from .. import mise_hook

    mise_hook.verify_hook_token(request)
    result = mise_hook.recommend_published_gallery(
        mise_gallery_id=mise_gallery_id,
        tenant_id=tenant_id,
        argus_run_id=argus_run_id,
        limit=limit,
    )
    scope = mise_hook.resolve_hook_tenant_id(tenant_id)
    if scope:
        metrics.inc_tenant(scope, "recommend_mise")
        audit.record(
            "recommend.mise.hook",
            request=request,
            tenant_id=scope,
            resource=str(result["run_id"]),
            detail={"mise_gallery_id": mise_gallery_id},
        )
    return JSONResponse(result)



@router.post("/webhooks/stripe")
async def stripe_webhook(request: Request):
    payload = await request.body()
    sig = request.headers.get("stripe-signature")
    if not billing.verify_webhook_signature(payload, sig):
        return error("invalid stripe signature", 400)
    try:
        event = json.loads(payload.decode("utf-8"))
    except json.JSONDecodeError:
        return error("invalid json", 400)
    try:
        billing.handle_webhook_event(event)
    except Exception as exc:
        log.exception("stripe webhook processing failed")
        audit.record(
            "billing.webhook",
            request=request,
            status="error",
            detail={"type": event.get("type"), "error": str(exc)[:200]},
        )
        return error("webhook processing failed", 500)
    audit.record("billing.webhook", request=request, detail={"type": event.get("type")})
    return {"received": True}



@router.post("/webhooks/whcc")
async def whcc_webhook(request: Request):
    if not config.WHCC_WEBHOOK_SECRET:
        return error("whcc webhooks not configured", 400)
    token = request.headers.get("x-whcc-signature") or request.headers.get("authorization", "")
    from .. import lab_whcc

    if not lab_whcc.verify_webhook_token(token):
        return error("invalid whcc webhook auth", 401)
    try:
        payload = await request.json()
    except json.JSONDecodeError:
        return error("invalid json", 400)

    if not lab_whcc.handle_webhook(payload):
        return error("unhandled webhook", 404)
    audit.record("lab.whcc.webhook", request=request, detail=payload)
    return {"received": True}




