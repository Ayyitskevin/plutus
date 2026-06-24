from __future__ import annotations

import logging
from pathlib import Path
from urllib.parse import quote_plus

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse

from .. import (
    audit,
    config,
    db,
    homelab,
    lab,
    pitch,
    saas,
    service,
)
from ..storefront import StorefrontError, create_share_link
from .deps import (
    request_auth,
    templates,
    ui_context,
)

log = logging.getLogger("plutus")
router = APIRouter()

@router.get("/", response_class=HTMLResponse)
def home(request: Request):
    if config.SAAS_MODE:
        return RedirectResponse("/ui/saas", status_code=302)
    runs = db.list_runs(limit=10)
    return templates.TemplateResponse(
        request, "index.html", {"runs": runs, "title": "upsell"}
    )



@router.post("/analyze", response_class=HTMLResponse)
def analyze_form(
    request: Request,
    folder: str = Form(...),
    name: str | None = Form(None),
    argus_run_id: int | None = Form(None),
    limit: int | None = Form(None),
):
    if config.SAAS_MODE:
        raise HTTPException(status_code=403, detail="use tenant portal in SaaS mode")
    path = Path(folder).expanduser()
    try:
        result = service.analyze_folder(
            path, name=name, argus_run_id=argus_run_id, limit=limit
        )
    except FileNotFoundError:
        return templates.TemplateResponse(
            request,
            "index.html",
            {"error": f"Folder not found: {folder}", "runs": db.list_runs(limit=10)},
            status_code=400,
        )
    return RedirectResponse(f"/runs/{result['run_id']}", status_code=303)



@router.get("/runs/{run_id}", response_class=HTMLResponse)
def view_run(request: Request, run_id: int):
    ctx = request_auth(request)
    row = saas.get_run_for_ctx(run_id, ctx) if config.SAAS_MODE else db.get_run(run_id)
    if not row:
        return HTMLResponse("Run not found", status_code=404)
    payload = row["payload"]
    gallery_name = db.get_gallery_name(row["gallery_id"]) or f"Run {run_id}"
    pitch_text = pitch.render_pitch(
        gallery_name=gallery_name,
        bundles=payload.get("bundles") or [],
        estimated_total_cents=int(payload.get("estimated_total_cents") or 0),
        photo_count=int(payload.get("photo_count") or 0),
        gallery_theme=payload.get("gallery_theme"),
        argus_run_id=row.get("argus_run_id"),
    )
    share_links = []
    if config.SAAS_MODE and ctx and ctx.tenant_id:
        share_links = db.list_storefront_tokens(ctx.tenant_id, run_id=run_id)
    elif homelab.store_enabled():
        share_links = db.list_storefront_tokens(homelab.tenant_id(), run_id=run_id)
    return templates.TemplateResponse(
        request,
        "run.html",
        ui_context(request, 
            run=row,
            bundles=payload.get("bundles") or [],
            top_photos=payload.get("top_photos") or [],
            photo_count=payload.get("photo_count", 0),
            estimated_total_cents=payload.get("estimated_total_cents", 0),
            gallery_theme=payload.get("gallery_theme"),
            pitch_text=pitch_text,
            title=f"run {run_id}",
            share_links=share_links,
            tenant=ctx.tenant
            if ctx and ctx.tenant
            else (db.get_tenant(homelab.tenant_id()) if homelab.store_enabled() else None),
        ),
    )



@router.get("/runs/{run_id}/json", response_class=JSONResponse)
def run_json(request: Request, run_id: int):
    ctx = request_auth(request)
    row = saas.get_run_for_ctx(run_id, ctx) if config.SAAS_MODE else db.get_run(run_id)
    if not row:
        raise HTTPException(status_code=404, detail="run not found")
    return row



@router.get("/runs/{run_id}/pitch.txt", response_class=PlainTextResponse)
def run_pitch(request: Request, run_id: int):
    ctx = request_auth(request)
    row = saas.get_run_for_ctx(run_id, ctx) if config.SAAS_MODE else db.get_run(run_id)
    if not row:
        raise HTTPException(status_code=404, detail="run not found")
    payload = row["payload"]
    gallery_name = db.get_gallery_name(row["gallery_id"]) or f"Run {run_id}"
    return pitch.render_pitch(
        gallery_name=gallery_name,
        bundles=payload.get("bundles") or [],
        estimated_total_cents=int(payload.get("estimated_total_cents") or 0),
        photo_count=int(payload.get("photo_count") or 0),
        gallery_theme=payload.get("gallery_theme"),
        argus_run_id=row.get("argus_run_id"),
    )





@router.post("/ui/homelab/share-link")
def ui_homelab_create_share_link(
    request: Request,
    run_id: int = Form(...),
    label: str | None = Form(None),
):
    if not homelab.store_enabled():
        raise HTTPException(status_code=404, detail="homelab storefront not enabled")
    try:
        link = create_share_link(
            tenant_id=homelab.tenant_id(),
            run_id=run_id,
            label=label.strip() if label else None,
        )
    except StorefrontError as exc:
        return RedirectResponse(
            f"/runs/{run_id}?share_error={quote_plus(str(exc))}",
            status_code=303,
        )
    audit.record(
        "storefront.link.create",
        request=request,
        tenant_id=homelab.tenant_id(),
        resource=link["token"],
    )
    return RedirectResponse(
        f"/runs/{run_id}?share_created=1&offer_url={quote_plus(link['public_url'])}",
        status_code=303,
    )



@router.get("/ui/homelab/orders/{order_id}", response_class=HTMLResponse)
def ui_homelab_order_detail(request: Request, order_id: int):
    if not homelab.store_enabled():
        raise HTTPException(status_code=404, detail="homelab storefront not enabled")
    tenant_id = homelab.tenant_id()
    order = db.get_order(order_id, tenant_id=tenant_id)
    if not order:
        return HTMLResponse("Order not found", status_code=404)
    try:
        lab.poll_order(order_id)
    except lab.LabError:
        pass
    order = db.get_order(order_id, tenant_id=tenant_id) or order
    tenant = db.get_tenant(tenant_id)
    run = db.get_run(int(order["run_id"]), tenant_id=tenant_id)
    return templates.TemplateResponse(
        request,
        "saas_order.html",
        ui_context(request, 
            title=f"Order {order_id}",
            order=order,
            tenant=tenant,
            run=run,
            is_admin=False,
            fulfillment_events=db.list_fulfillment_events(order_id),
        ),
    )


