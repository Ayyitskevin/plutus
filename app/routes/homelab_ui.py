from __future__ import annotations

import logging
from pathlib import Path
from urllib.parse import quote_plus

from fastapi import APIRouter, Form, HTTPException, Query, Request
from fastapi.responses import (
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
    Response,
)

from .. import (
    audit,
    config,
    db,
    homelab,
    lab,
    notifications,
    pitch,
    saas,
    service,
)
from ..bundle_editor import BundleEditError, photos_for_run, save_run_edits
from ..gallery_media import (
    FULL_MAX_EDGE,
    THUMB_MAX_EDGE,
    GalleryMediaError,
    enrich_bundles_for_run,
    enrich_top_photos_for_run,
    render_jpeg,
    resolve_photo_file,
)
from ..order_views import enrich_order_bundle
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
    return templates.TemplateResponse(request, "index.html", {"runs": runs, "title": "upsell"})


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
        result = service.analyze_folder(path, name=name, argus_run_id=argus_run_id, limit=limit)
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
    gallery = db.get_gallery(row["gallery_id"])
    mise_gallery_id = gallery.get("mise_gallery_id") if gallery else None
    mise_gallery_url = (
        f"{config.MISE_ADMIN_URL}/admin/galleries/{mise_gallery_id}"
        if config.MISE_ADMIN_URL and mise_gallery_id
        else None
    )
    bundles = enrich_bundles_for_run(run_id, payload.get("bundles") or [])
    top_photos = enrich_top_photos_for_run(run_id, payload.get("top_photos") or [])
    return templates.TemplateResponse(
        request,
        "run.html",
        ui_context(
            request,
            run=row,
            mise_gallery_url=mise_gallery_url,
            bundles=bundles,
            top_photos=top_photos,
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


def _run_edit_context(request: Request, run_id: int, **extra):
    ctx = request_auth(request)
    row = saas.get_run_for_ctx(run_id, ctx) if config.SAAS_MODE else db.get_run(run_id)
    if not row:
        return None
    gallery_name = db.get_gallery_name(row["gallery_id"]) or f"Run {run_id}"
    payload = row["payload"]
    tenant_id = ctx.tenant_id if ctx and ctx.tenant_id else None
    if config.SAAS_MODE:
        save_action = "/ui/saas/app/run-edit"
        sell_url = "/ui/saas/app/sell?run_id=" + str(run_id) if ctx and ctx.tenant_id else None
    elif homelab.store_enabled():
        save_action = "/ui/homelab/run-edit"
        sell_url = None
    else:
        save_action = "/ui/homelab/run-edit"
        sell_url = None
    return ui_context(
        request,
        run=row,
        bundles=payload.get("bundles") or [],
        gallery_photos=photos_for_run(row),
        gallery_name=gallery_name,
        title=f"Edit run {run_id}",
        save_action=save_action,
        sell_url=sell_url,
        tenant_id=tenant_id,
        **extra,
    )


@router.get("/runs/{run_id}/edit", response_class=HTMLResponse)
def edit_run(request: Request, run_id: int):
    ctx_data = _run_edit_context(request, run_id)
    if not ctx_data:
        return HTMLResponse("Run not found", status_code=404)
    return templates.TemplateResponse(request, "run_edit.html", ctx_data)


@router.post("/ui/homelab/run-edit")
async def ui_homelab_run_edit(request: Request, run_id: int = Form(...)):
    if config.SAAS_MODE:
        raise HTTPException(status_code=404, detail="use tenant portal")
    tenant_id = homelab.tenant_id() if homelab.store_enabled() else None
    try:
        from ..bundle_editor import parse_bundle_form

        save_run_edits(
            run_id=run_id,
            tenant_id=tenant_id,
            bundle_edits=parse_bundle_form(await request.form()),
        )
    except BundleEditError as exc:
        ctx_data = _run_edit_context(request, run_id, edit_error=str(exc))
        if not ctx_data:
            return HTMLResponse("Run not found", status_code=404)
        return templates.TemplateResponse(request, "run_edit.html", ctx_data, status_code=400)
    return RedirectResponse(f"/runs/{run_id}?edited=1", status_code=303)


@router.get("/runs/{run_id}/photo/{filename}")
def run_photo(request: Request, run_id: int, filename: str, size: str = Query("thumb")):
    ctx = request_auth(request)
    row = saas.get_run_for_ctx(run_id, ctx) if config.SAAS_MODE else db.get_run(run_id)
    if not row:
        return Response(status_code=404)
    gallery = db.get_gallery(row["gallery_id"])
    try:
        path = resolve_photo_file(
            gallery=gallery,
            payload=row["payload"],
            filename=filename,
        )
        max_edge = FULL_MAX_EDGE if size == "full" else THUMB_MAX_EDGE
        data = render_jpeg(path, max_edge=max_edge)
    except GalleryMediaError:
        return Response(status_code=404)
    return Response(
        content=data,
        media_type="image/jpeg",
        headers={
            "Cache-Control": "private, max-age=3600",
            "Content-Disposition": f'inline; filename="{path.name}"',
        },
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
    bundle_display = enrich_order_bundle(
        order,
        run,
        photo_base=f"/ui/homelab/orders/{order_id}/photo",
    )
    return templates.TemplateResponse(
        request,
        "saas_order.html",
        ui_context(
            request,
            title=f"Order {order_id}",
            order=order,
            tenant=tenant,
            run=run,
            bundle_display=bundle_display,
            is_admin=False,
            smtp_ready=notifications.smtp_ready(),
            fulfillment_events=db.list_fulfillment_events(order_id),
            order_message="Lab status refreshed."
            if request.query_params.get("lab_polled")
            else "Client confirmation resent."
            if request.query_params.get("resent")
            else None,
            order_error=request.query_params.get("order_error"),
        ),
    )


@router.get("/ui/homelab/orders/{order_id}/photo/{filename}")
def ui_homelab_order_photo(order_id: int, filename: str, size: str = Query("thumb")):
    if not homelab.store_enabled():
        raise HTTPException(status_code=404, detail="homelab storefront not enabled")
    tenant_id = homelab.tenant_id()
    order = db.get_order(order_id, tenant_id=tenant_id)
    if not order:
        return Response(status_code=404)
    run = db.get_run(int(order["run_id"]), tenant_id=tenant_id)
    if not run:
        return Response(status_code=404)
    gallery = db.get_gallery(int(run["gallery_id"]))
    try:
        path = resolve_photo_file(
            gallery=gallery,
            payload=run["payload"],
            filename=filename,
        )
        max_edge = FULL_MAX_EDGE if size == "full" else THUMB_MAX_EDGE
        data = render_jpeg(path, max_edge=max_edge)
    except GalleryMediaError:
        return Response(status_code=404)
    return Response(
        content=data,
        media_type="image/jpeg",
        headers={"Cache-Control": "private, max-age=3600"},
    )


@router.post("/ui/homelab/orders/{order_id}/poll-lab")
def ui_homelab_order_poll_lab(order_id: int):
    if not homelab.store_enabled():
        raise HTTPException(status_code=404, detail="homelab storefront not enabled")
    tenant_id = homelab.tenant_id()
    order = db.get_order(order_id, tenant_id=tenant_id)
    if not order:
        return RedirectResponse("/?order_error=order+not+found", status_code=303)
    try:
        lab.poll_order(order_id)
    except lab.LabError:
        return RedirectResponse(
            f"/ui/homelab/orders/{order_id}?order_error=lab+poll+failed",
            status_code=303,
        )
    return RedirectResponse(f"/ui/homelab/orders/{order_id}?lab_polled=1", status_code=303)


@router.post("/ui/homelab/orders/{order_id}/resend-confirmation")
def ui_homelab_order_resend_confirmation(order_id: int):
    if not homelab.store_enabled():
        raise HTTPException(status_code=404, detail="homelab storefront not enabled")
    tenant_id = homelab.tenant_id()
    order = db.get_order(order_id, tenant_id=tenant_id)
    if not order:
        return RedirectResponse("/?order_error=order+not+found", status_code=303)
    if not notifications.smtp_ready():
        return RedirectResponse(
            f"/ui/homelab/orders/{order_id}?order_error=smtp+not+configured",
            status_code=303,
        )
    if not order.get("client_email"):
        return RedirectResponse(
            f"/ui/homelab/orders/{order_id}?order_error=no+client+email",
            status_code=303,
        )
    if not notifications.resend_client_confirmation(order_id):
        return RedirectResponse(
            f"/ui/homelab/orders/{order_id}?order_error=resend+failed",
            status_code=303,
        )
    return RedirectResponse(f"/ui/homelab/orders/{order_id}?resent=1", status_code=303)
