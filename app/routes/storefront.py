from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from .. import (
    audit,
    billing,
    catalog,
    config,
    db,
    homelab,
    lab,
    metrics,
    order_tracking,
)
from ..auth import require_bearer
from ..auth_context import AuthContext
from ..gallery_media import (
    FULL_MAX_EDGE,
    THUMB_MAX_EDGE,
    GalleryMediaError,
    render_jpeg,
    resolve_photo_file,
)
from ..orders import OrderError, create_bundle_checkout, simulate_test_payment
from ..storefront import (
    StorefrontError,
    create_share_link,
    link_tenant_for_bearer,
    resolve_offer,
)
from .deps import (
    templates,
    ui_context,
)

log = logging.getLogger("plutus")
router = APIRouter()

@router.get("/store/{slug}", response_class=HTMLResponse)
def store_landing(request: Request, slug: str):
    tenant = db.get_tenant_by_slug(slug)
    if not tenant:
        return HTMLResponse("Store not found", status_code=404)
    return templates.TemplateResponse(
        request,
        "store_landing.html",
        ui_context(request, tenant=tenant, title=tenant["name"]),
    )



@router.get("/store/{slug}/offer/{token}", response_class=HTMLResponse)
def store_offer(request: Request, slug: str, token: str):
    try:
        offer = resolve_offer(slug, token)
    except StorefrontError as exc:
        return HTMLResponse(str(exc), status_code=404)
    metrics.inc("storefront_views")
    metrics.inc_tenant(offer["tenant"]["id"], "storefront_views")
    return templates.TemplateResponse(
        request,
        "store_offer.html",
        ui_context(request, 
            title=offer["gallery_name"],
            tenant=offer["tenant"],
            gallery_name=offer["gallery_name"],
            gallery_theme=offer["run"]["payload"].get("gallery_theme"),
            bundles=offer["bundles"],
            token=token,
            slug=slug,
            run_id=offer["run"]["id"],
            stripe_enabled=billing.stripe_configured(),
            mnemosyne_url=config.MNEMOSYNE_URL,
            show_mnemosyne_cta=bool(
                config.MNEMOSYNE_URL and catalog.bundles_include_album(offer["bundles"])
            ),
        ),
    )



@router.get("/store/{slug}/offer/{token}/photo/{filename}")
def store_offer_photo(
    slug: str,
    token: str,
    filename: str,
    size: str = Query("thumb"),
):
    try:
        offer = resolve_offer(slug, token)
        path = resolve_photo_file(
            gallery=offer.get("gallery"),
            payload=offer["run"]["payload"],
            filename=filename,
        )
        max_edge = FULL_MAX_EDGE if size == "full" else THUMB_MAX_EDGE
        data = render_jpeg(path, max_edge=max_edge)
    except (StorefrontError, GalleryMediaError):
        return Response(status_code=404)
    return Response(
        content=data,
        media_type="image/jpeg",
        headers={
            "Cache-Control": "private, max-age=3600",
            "Content-Disposition": f'inline; filename="{path.name}"',
        },
    )



@router.post("/store/{slug}/offer/{token}/checkout")
def store_checkout(
    request: Request,
    slug: str,
    token: str,
    bundle_index: int = Form(...),
    client_email: str | None = Form(None),
    client_name: str | None = Form(None),
):
    try:
        offer = resolve_offer(slug, token)
        session = create_bundle_checkout(
            tenant_id=offer["tenant"]["id"],
            run_id=int(offer["run"]["id"]),
            bundle_index=bundle_index,
            client_email=client_email,
            client_name=client_name,
        )
    except (StorefrontError, OrderError) as exc:
        return HTMLResponse(str(exc), status_code=400)
    audit.record(
        "store.checkout",
        request=request,
        tenant_id=offer["tenant"]["id"],
        resource=str(session["order_id"]),
    )
    return RedirectResponse(session["checkout_url"], status_code=303)



@router.get("/store/order/track/{client_token}", response_class=HTMLResponse)
def store_order_track(request: Request, client_token: str):
    order = order_tracking.resolve_public_order(client_token)
    if not order:
        return HTMLResponse("Order not found", status_code=404)
    try:
        lab.poll_order(int(order["id"]))
    except lab.LabError:
        pass
    order = order_tracking.resolve_public_order(client_token) or order
    tenant = db.get_tenant(order["tenant_id"])
    run = db.get_run(int(order["run_id"]), tenant_id=order["tenant_id"])
    bundle_title = None
    if run:
        bundles = (run.get("payload") or {}).get("bundles") or []
        idx = int(order.get("bundle_index") or 0)
        if 0 <= idx < len(bundles):
            bundle_title = bundles[idx].get("title")
    return templates.TemplateResponse(
        request,
        "client_order.html",
        ui_context(request, 
            title="Your order",
            order=order,
            tenant=tenant,
            bundle_title=bundle_title,
            fulfillment_events=db.list_fulfillment_events(int(order["id"])),
        ),
    )



@router.get("/store/order/success", response_class=HTMLResponse)
def store_order_success(request: Request, session_id: str | None = Query(None)):
    order = db.get_order_by_session(session_id) if session_id else None
    if order and order.get("status") == "paid":
        try:
            lab.poll_order(int(order["id"]))
        except lab.LabError:
            pass
        order = db.get_order(int(order["id"])) or order
    fulfillment_events = (
        db.list_fulfillment_events(int(order["id"])) if order and order.get("id") else []
    )
    track_url = None
    if order and order.get("client_token"):
        track_url = order_tracking.client_track_url(str(order["client_token"]))
    return templates.TemplateResponse(
        request,
        "store_order.html",
        ui_context(request, 
            title="Order confirmed",
            order=order,
            success=True,
            fulfillment_events=fulfillment_events,
            client_track_url=track_url,
        ),
    )



@router.get("/store/order/cancelled", response_class=HTMLResponse)
def store_order_cancelled(request: Request, order_id: int | None = Query(None)):
    order = db.get_order(order_id) if order_id else None
    return templates.TemplateResponse(
        request,
        "store_order.html",
        ui_context(request, title="Checkout cancelled", order=order, success=False),
    )





@router.post("/storefront/share-links", response_class=JSONResponse)
def api_create_share_link(
    run_id: int = Form(...),
    label: str | None = Form(None),
    tenant_id: str | None = Form(None),
    ctx: AuthContext = Depends(require_bearer),
) -> JSONResponse:
    link_tenant = link_tenant_for_bearer(ctx, tenant_id)
    if not link_tenant:
        raise HTTPException(
            status_code=403,
            detail="tenant API key required (or admin with tenant_id)",
        )
    try:
        link = create_share_link(
            tenant_id=link_tenant,
            run_id=run_id,
            label=label.strip() if label else None,
        )
    except StorefrontError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return JSONResponse(link)



@router.post("/orders/{order_id}/simulate-payment", response_class=JSONResponse)
def api_simulate_payment(
    order_id: int,
    ctx: AuthContext = Depends(require_bearer),
) -> JSONResponse:
    if not config.SAAS_MODE and not homelab.store_enabled():
        raise HTTPException(status_code=403, detail="store checkout not enabled")
    if homelab.store_enabled() and ctx.is_admin:
        tenant_scope = homelab.tenant_id()
    else:
        tenant_scope = None if ctx.is_admin else ctx.tenant_id
    order = db.get_order(order_id, tenant_id=tenant_scope)
    if not order:
        raise HTTPException(status_code=404, detail="order not found")
    try:
        result = simulate_test_payment(order_id)
    except OrderError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return JSONResponse(result)


