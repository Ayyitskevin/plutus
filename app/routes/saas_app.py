from __future__ import annotations

import logging

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from .. import (
    catalog,
    config,
    db,
    lab,
    notifications,
)
from ..auth_context import AuthContext
from ..gallery_media import (
    FULL_MAX_EDGE,
    THUMB_MAX_EDGE,
    GalleryMediaError,
    render_jpeg,
    resolve_photo_file,
)
from ..order_views import bundle_title_for_order, enrich_order_bundle
from .deps import (
    admin_tenant_context,
    admin_ui_redirect,
    templates,
    tenant_ui_redirect,
    ui_context,
    ui_saas_auth,
)

log = logging.getLogger("plutus")
router = APIRouter()

@router.get("/ui/saas/app", response_class=HTMLResponse)
def ui_saas_tenant_app(request: Request):
    ctx = tenant_ui_redirect(request)
    if not isinstance(ctx, AuthContext):
        return ctx
    from ..metering import usage_snapshot

    usage = usage_snapshot(ctx.tenant_id)
    recent = db.list_runs(limit=10, tenant_id=ctx.tenant_id)
    upload_batches = db.list_upload_batches(tenant_id=ctx.tenant_id, limit=10)
    active_batches = [
        b for b in upload_batches if b["status"] in {"queued", "analyzing", "failed"}
    ]
    show_onboarding = not recent and not any(
        b["status"] in {"analyzed", "queued", "analyzing"} for b in upload_batches
    )
    tenant_keys = []
    for row in db.list_tenant_keys(ctx.tenant_id):
        item = dict(row)
        item["is_current"] = item["id"] == ctx.api_key_id
        tenant_keys.append(item)
    orders_list = []
    for row in db.list_orders(tenant_id=ctx.tenant_id, limit=10):
        item = dict(row)
        run = db.get_run(int(row["run_id"]), tenant_id=ctx.tenant_id)
        item["bundle_title"] = bundle_title_for_order(row, run)
        orders_list.append(item)
    tenant = db.get_tenant(ctx.tenant_id) or ctx.tenant
    return templates.TemplateResponse(
        request,
        "saas_dashboard.html",
        ui_context(request, 
            title="Dashboard",
            portal_mode="tenant",
            tenant=tenant,
            usage=usage,
            cap_warnings=usage.get("warnings") or [],
            recent_runs=recent,
            active_batches=active_batches,
            show_onboarding=show_onboarding,
            tenant_keys=tenant_keys,
            orders=orders_list,
            audit_events=db.list_audit_events(tenant_id=ctx.tenant_id, limit=10),
            tenant_message="API key revoked." if request.query_params.get("keys_updated") else None,
            tenant_error=request.query_params.get("keys_error"),
            settings_message="Notification email saved."
            if request.query_params.get("settings_saved")
            else None,
            settings_error=request.query_params.get("settings_error"),
            smtp_ready=notifications.smtp_ready(),
            smtp_from=config.SMTP_FROM or "",
            notification_test_message="Test email sent."
            if request.query_params.get("notification_test_sent")
            else None,
            notification_test_error=request.query_params.get("notification_test_error"),
        ),
    )



@router.get("/ui/saas/app/admin", response_class=HTMLResponse)
def ui_saas_admin_app(request: Request):
    ctx = admin_ui_redirect(request)
    if not isinstance(ctx, AuthContext):
        return ctx
    return templates.TemplateResponse(
        request,
        "saas_dashboard.html",
        ui_context(request, 
            title="Admin",
            portal_mode="admin",
            tenants=db.list_tenants(),
            global_usage=db.global_usage_totals(),
            orders=db.list_orders(limit=20),
            audit_events=db.list_audit_events(limit=30),
            admin_message=f"Tenant {request.query_params['created']} created."
            if request.query_params.get("created")
            else None,
            admin_error=request.query_params.get("error"),
        ),
    )



@router.get("/ui/saas/app/admin/tenants/{tenant_id}", response_class=HTMLResponse)
def ui_saas_admin_tenant(
    request: Request,
    tenant_id: str,
    updated: str | None = Query(None),
    revoked: str | None = Query(None),
    error: str | None = Query(None),
):
    ctx = admin_ui_redirect(request)
    if not isinstance(ctx, AuthContext):
        return ctx
    if not db.get_tenant(tenant_id):
        return RedirectResponse("/ui/saas/app/admin?error=tenant+not+found", status_code=303)
    admin_message = None
    if updated:
        admin_message = "Settings saved."
    elif revoked:
        admin_message = "API key revoked."
    return templates.TemplateResponse(
        request,
        "saas_admin_tenant.html",
        admin_tenant_context(
            request,
            tenant_id,
            admin_message=admin_message,
            admin_error=error,
        ),
    )



@router.get("/ui/saas/app/sell", response_class=HTMLResponse)
def ui_saas_sell(
    request: Request,
    run_id: int | None = Query(None),
    analyzing: str | None = Query(None),
    published: str | None = Query(None),
    offer_url: str | None = Query(None),
    auto: str | None = Query(None),
):
    ctx = tenant_ui_redirect(request)
    if not isinstance(ctx, AuthContext):
        return ctx
    run = None
    bundles: list[dict] = []
    if run_id:
        run = db.get_run(run_id, tenant_id=ctx.tenant_id)
        if run:
            bundles = (run.get("payload") or {}).get("bundles") or []
    recent_runs = db.list_runs(limit=8, tenant_id=ctx.tenant_id)
    step = 3 if published and offer_url else (2 if run_id and run else 1)
    return templates.TemplateResponse(
        request,
        "saas_sell.html",
        ui_context(request, 
            title="Publish & sell",
            tenant=ctx.tenant,
            step=step,
            run=run,
            run_id=run_id,
            bundles=bundles,
            recent_runs=recent_runs,
            analyzing_batch_id=analyzing,
            auto_publish=auto == "1",
            offer_url=offer_url,
            sell_message="Client offer link is ready — copy and send to your client."
            if published
            else None,
            sell_error=request.query_params.get("error"),
            max_files=config.MAX_UPLOAD_FILES,
            max_mb=config.MAX_UPLOAD_FILE_BYTES // (1024 * 1024),
        ),
    )



@router.get("/ui/saas/app/upload", response_class=HTMLResponse)
def ui_saas_upload(
    request: Request,
    uploaded: str | None = Query(None),
    analyzing: str | None = Query(None),
):
    ctx = tenant_ui_redirect(request)
    if not isinstance(ctx, AuthContext):
        return ctx
    return templates.TemplateResponse(
        request,
        "saas_upload.html",
        ui_context(request, 
            title="Upload gallery",
            tenant=ctx.tenant,
            batches=db.list_upload_batches(tenant_id=ctx.tenant_id, limit=10),
            max_files=config.MAX_UPLOAD_FILES,
            max_mb=config.MAX_UPLOAD_FILE_BYTES // (1024 * 1024),
            upload_message="Photos uploaded — analyze when ready."
            if uploaded
            else None,
            analyzing_batch_id=analyzing,
        ),
    )



@router.get("/ui/saas/app/mise", response_class=HTMLResponse)
def ui_saas_mise_galleries(request: Request):
    from .. import mise_client

    ctx = tenant_ui_redirect(request)
    if not isinstance(ctx, AuthContext):
        return ctx
    galleries: list[dict] = []
    mise_error: str | None = None
    if mise_client.is_enabled():
        try:
            body = mise_client.list_galleries(published=False)
            galleries = body.get("galleries") or []
        except mise_client.MiseClientError as exc:
            mise_error = str(exc)
    return templates.TemplateResponse(
        request,
        "saas_mise.html",
        ui_context(request, 
            title="Mise galleries",
            tenant=ctx.tenant,
            galleries=galleries,
            mise_message=(
                "Bundles generated"
                + (
                    " — client offer ready."
                    if request.query_params.get("offer_url")
                    else " — publish an offer from Publish & sell."
                )
            )
            if request.query_params.get("recommended")
            else None,
            mise_run_id=request.query_params.get("run_id"),
            mise_offer_url=request.query_params.get("offer_url"),
            mise_error=mise_error or request.query_params.get("error"),
        ),
    )



@router.get("/ui/saas/app/catalog", response_class=HTMLResponse)
def ui_saas_tenant_catalog(
    request: Request,
    saved: str | None = Query(None),
    error: str | None = Query(None),
):
    ctx = tenant_ui_redirect(request)
    if not isinstance(ctx, AuthContext):
        return ctx
    return templates.TemplateResponse(
        request,
        "saas_catalog.html",
        ui_context(request, 
            title="Product pricing",
            tenant=ctx.tenant,
            products=catalog.list_catalog(ctx.tenant_id),
            catalog_message="Pricing saved." if saved else None,
            catalog_error=error,
        ),
    )



@router.get("/ui/saas/app/orders/{order_id}", response_class=HTMLResponse)
def ui_saas_order_detail(request: Request, order_id: int):
    ctx = ui_saas_auth(request)
    if ctx is None:
        return RedirectResponse("/ui/saas/login", status_code=303)
    tenant_id = None if ctx.is_admin else ctx.tenant_id
    order = db.get_order(order_id, tenant_id=tenant_id)
    if not order:
        return HTMLResponse("Order not found", status_code=404)
    try:
        lab.poll_order(order_id)
    except lab.LabError:
        pass
    order = db.get_order(order_id, tenant_id=tenant_id) or order
    tenant = db.get_tenant(order["tenant_id"])
    run = db.get_run(int(order["run_id"]), tenant_id=order["tenant_id"])
    bundle_display = enrich_order_bundle(
        order,
        run,
        photo_base=f"/ui/saas/app/orders/{order_id}/photo",
    )
    return templates.TemplateResponse(
        request,
        "saas_order.html",
        ui_context(request, 
            title=f"Order {order_id}",
            order=order,
            tenant=tenant,
            run=run,
            bundle_display=bundle_display,
            is_admin=ctx.is_admin,
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


@router.get("/ui/saas/app/orders/{order_id}/photo/{filename}")
def ui_saas_order_photo(request: Request, order_id: int, filename: str, size: str = Query("thumb")):
    ctx = ui_saas_auth(request)
    if ctx is None:
        return Response(status_code=401)
    tenant_id = None if ctx.is_admin else ctx.tenant_id
    order = db.get_order(order_id, tenant_id=tenant_id)
    if not order:
        return Response(status_code=404)
    run = db.get_run(int(order["run_id"]), tenant_id=order["tenant_id"])
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


