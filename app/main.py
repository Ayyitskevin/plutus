"""Plutus FastAPI app — print & album upsell recommendations."""
from __future__ import annotations

import json
import logging
import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote_plus

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import (
    audit,
    billing,
    catalog,
    config,
    db,
    health,
    homelab,
    lab,
    metrics,
    order_tracking,
    pitch,
    rate_limit,
    saas,
    sell,
    service,
    signup,
    storage,
    ui_sessions,
    upload_worker,
    uploads,
)
from .auth import UI_TOKEN_COOKIE, require_bearer, resolve_auth, verify_ui_csrf
from .auth_context import AuthContext
from .metering import MeteringError
from .orders import OrderError, create_bundle_checkout, simulate_test_payment
from .sell import SellError
from .storefront import StorefrontError, create_share_link, resolve_offer
from .tenants import TenantError

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("plutus")

_ROOT = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=str(_ROOT / "templates"))


def _fmt_cents(cents: int) -> str:
    return f"${cents / 100:,.2f}"


templates.env.filters["money"] = _fmt_cents


def _ui_context(request: Request | None = None, **extra) -> dict:
    from . import argus_client, mise_client

    ctx = {
        "saas_mode": config.SAAS_MODE,
        "billing_enabled": billing.billing_enabled()
        if config.SAAS_MODE or homelab.store_enabled()
        else False,
        "homelab_store": homelab.store_enabled(),
        "signup_enabled": signup.signup_enabled(),
        "storage": storage.storage_status(),
        "argus": argus_client.vision_status() if argus_client.is_enabled() else None,
        "argus_auto_vision": config.ARGUS_AUTO_VISION,
        "mise_configured": mise_client.is_enabled(),
        "public_base": config.SAAS_PUBLIC_URL.rstrip("/"),
        "upload_async": config.UPLOAD_ASYNC_ANALYZE,
        "csrf_token": "",
    }
    if request is not None:
        session = ui_sessions.get_session(request.cookies.get(ui_sessions.UI_SESSION_COOKIE))
        if session:
            ctx["csrf_token"] = session.get("csrf_token") or ""
    ctx.update(extra)
    return ctx


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    saas.validate_saas_startup()
    db.migrate()
    if homelab.store_enabled():
        homelab.ensure_bootstrap()
    stop_poll = threading.Event()

    def _lab_poll_loop() -> None:
        while not stop_poll.wait(60):
            try:
                lab.poll_pending_orders()
            except Exception:
                log.exception("lab poll loop error")

    poll_thread = None
    if lab.lab_enabled() and config.LAB_ADAPTER in {"mock", "whcc"}:
        if config.SAAS_MODE or homelab.store_enabled():
            poll_thread = threading.Thread(target=_lab_poll_loop, daemon=True, name="lab-poll")
            poll_thread.start()

    def _upload_worker_loop() -> None:
        while not stop_poll.wait(config.UPLOAD_WORKER_INTERVAL):
            try:
                upload_worker.process_pending_batches()
            except Exception:
                log.exception("upload worker loop error")

    upload_thread = None
    if config.SAAS_MODE and config.UPLOAD_ASYNC_ANALYZE:
        upload_thread = threading.Thread(
            target=_upload_worker_loop, daemon=True, name="upload-worker"
        )
        upload_thread.start()
    try:
        yield
    finally:
        stop_poll.set()
        grace = config.SHUTDOWN_GRACE_SECONDS
        if poll_thread:
            poll_thread.join(timeout=grace)
        if upload_thread:
            upload_thread.join(timeout=grace)


app = FastAPI(
    title="plutus",
    version="1.0.0",
    description=(
        "Print & album upsell for photo galleries. "
        "SaaS tenants authenticate with `Authorization: Bearer plutus_tk_<tenant>_<token>`."
    ),
    lifespan=lifespan,
)

if config.CORS_ORIGINS:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=config.CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
app.middleware("http")(rate_limit.rate_limit_middleware)
app.middleware("http")(saas.saas_auth_middleware)
app.mount("/static", StaticFiles(directory=str(_ROOT / "static")), name="static")


def _request_auth(request: Request) -> AuthContext | None:
    return getattr(request.state, "auth", None)


def _ui_saas_auth(request: Request) -> AuthContext | None:
    ctx = _request_auth(request)
    if ctx is not None:
        return ctx
    try:
        return resolve_auth(request)
    except HTTPException:
        return None


def error(message: str, status_code: int) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status_code)


# --- Health & metrics ---


@app.get("/healthz")
def healthz() -> dict:
    from . import mise_client

    report = health.build_health_report()
    report.update({
        "service": "plutus",
        "engine": "mock",
        "mise_configured": mise_client.is_enabled(),
        "auth_enabled": bool(config.API_TOKEN),
    })
    return report


@app.get("/saas/status")
def saas_status() -> dict:
    return {
        "saas_mode": config.SAAS_MODE,
        "billing": billing.billing_status(),
        "signup_enabled": signup.signup_enabled(),
        "lab": lab.fulfillment_status(),
    }


@app.get("/saas/billing/status")
def saas_billing_status() -> dict:
    return billing.billing_status()


@app.get("/metrics")
def metrics_endpoint() -> PlainTextResponse:
    if not config.PROMETHEUS_ENABLED:
        raise HTTPException(status_code=404, detail="prometheus disabled")
    return PlainTextResponse(metrics.prometheus_text(), media_type="text/plain; version=0.0.4")


# --- API routes ---


@app.post("/recommend/mise-gallery")
def recommend_mise_gallery_api(
    request: Request,
    mise_gallery_id: int = Form(...),
    limit: int | None = Form(None),
    argus_run_id: int | None = Form(None),
    tenant_id: str | None = Form(None),
    ctx: AuthContext = Depends(require_bearer),
) -> JSONResponse:
    from . import mise_hook

    if config.SAAS_MODE and ctx.is_admin:
        scope = mise_hook.resolve_hook_tenant_id(tenant_id)
    elif config.SAAS_MODE:
        scope = ctx.tenant_id
    elif homelab.store_enabled():
        homelab.ensure_bootstrap()
        scope = homelab.tenant_id()
    else:
        scope = None
    result = mise_hook.recommend_published_gallery(
        mise_gallery_id=mise_gallery_id,
        tenant_id=scope,
        argus_run_id=argus_run_id,
        limit=limit,
    )
    if scope:
        metrics.inc_tenant(scope, "recommend_mise")
        audit.record("recommend.mise", request=request, ctx=ctx, resource=str(result["run_id"]))
    return JSONResponse(result)


@app.post("/webhooks/mise/gallery-published")
def mise_gallery_published_webhook(
    request: Request,
    mise_gallery_id: int = Form(...),
    tenant_id: str | None = Form(None),
    argus_run_id: int | None = Form(None),
    limit: int | None = Form(None),
) -> JSONResponse:
    from . import mise_hook

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


@app.post("/recommend/upload-batch")
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
        result = service.analyze_upload_batch(
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


@app.get("/upload-batches/{batch_id}/status", response_class=JSONResponse)
def upload_batch_status_api(batch_id: str, ctx: AuthContext = Depends(require_bearer)):
    if not config.SAAS_MODE or not ctx.tenant_id:
        raise HTTPException(status_code=403, detail="tenant API key required")
    try:
        return service.upload_batch_status(batch_id, tenant_id=ctx.tenant_id)
    except service.RecommendError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/analyze-folder")
def analyze_folder_api(
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
        result = service.analyze_folder(
            path, name=name, argus_run_id=argus_run_id, limit=limit
        )
    except MeteringError as exc:
        raise HTTPException(status_code=402, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    metrics.inc("recommend_folder")
    return JSONResponse(result)


# --- Homelab UI ---


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    if config.SAAS_MODE:
        return RedirectResponse("/ui/saas", status_code=302)
    runs = db.list_runs(limit=10)
    return templates.TemplateResponse(
        request, "index.html", {"runs": runs, "title": "upsell"}
    )


@app.post("/analyze", response_class=HTMLResponse)
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


@app.get("/runs/{run_id}", response_class=HTMLResponse)
def view_run(request: Request, run_id: int):
    ctx = _request_auth(request)
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
        _ui_context(request, 
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


@app.get("/runs/{run_id}/json", response_class=JSONResponse)
def run_json(request: Request, run_id: int):
    ctx = _request_auth(request)
    row = saas.get_run_for_ctx(run_id, ctx) if config.SAAS_MODE else db.get_run(run_id)
    if not row:
        raise HTTPException(status_code=404, detail="run not found")
    return row


@app.get("/runs/{run_id}/pitch.txt", response_class=PlainTextResponse)
def run_pitch(request: Request, run_id: int):
    ctx = _request_auth(request)
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


# --- Storefront (public) ---


@app.get("/store/{slug}", response_class=HTMLResponse)
def store_landing(request: Request, slug: str):
    tenant = db.get_tenant_by_slug(slug)
    if not tenant:
        return HTMLResponse("Store not found", status_code=404)
    return templates.TemplateResponse(
        request,
        "store_landing.html",
        _ui_context(request, tenant=tenant, title=tenant["name"]),
    )


@app.get("/store/{slug}/offer/{token}", response_class=HTMLResponse)
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
        _ui_context(request, 
            title=offer["gallery_name"],
            tenant=offer["tenant"],
            gallery_name=offer["gallery_name"],
            gallery_theme=offer["run"]["payload"].get("gallery_theme"),
            bundles=offer["bundles"],
            token=token,
            slug=slug,
            run_id=offer["run"]["id"],
            stripe_enabled=billing.stripe_configured(),
        ),
    )


@app.post("/store/{slug}/offer/{token}/checkout")
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


@app.get("/store/order/track/{client_token}", response_class=HTMLResponse)
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
        _ui_context(request, 
            title="Your order",
            order=order,
            tenant=tenant,
            bundle_title=bundle_title,
            fulfillment_events=db.list_fulfillment_events(int(order["id"])),
        ),
    )


@app.get("/store/order/success", response_class=HTMLResponse)
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
        _ui_context(request, 
            title="Order confirmed",
            order=order,
            success=True,
            fulfillment_events=fulfillment_events,
            client_track_url=track_url,
        ),
    )


@app.get("/store/order/cancelled", response_class=HTMLResponse)
def store_order_cancelled(request: Request, order_id: int | None = Query(None)):
    order = db.get_order(order_id) if order_id else None
    return templates.TemplateResponse(
        request,
        "store_order.html",
        _ui_context(request, title="Checkout cancelled", order=order, success=False),
    )


# --- Stripe webhook ---


@app.post("/storefront/share-links", response_class=JSONResponse)
def api_create_share_link(
    run_id: int = Form(...),
    label: str | None = Form(None),
    ctx: AuthContext = Depends(require_bearer),
) -> JSONResponse:
    link_tenant = ctx.tenant_id
    if not link_tenant:
        if homelab.store_enabled() and ctx.is_admin:
            link_tenant = homelab.tenant_id()
        else:
            raise HTTPException(status_code=403, detail="tenant API key required")
    try:
        link = create_share_link(
            tenant_id=link_tenant,
            run_id=run_id,
            label=label.strip() if label else None,
        )
    except StorefrontError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return JSONResponse(link)


@app.post("/orders/{order_id}/simulate-payment", response_class=JSONResponse)
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


@app.post("/webhooks/stripe")
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


@app.post("/webhooks/whcc")
async def whcc_webhook(request: Request):
    if not config.WHCC_WEBHOOK_SECRET:
        return error("whcc webhooks not configured", 400)
    token = request.headers.get("x-whcc-signature") or request.headers.get("authorization", "")
    from . import lab_whcc

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


# --- SaaS portal ---


@app.get("/ui/saas", response_class=HTMLResponse)
def ui_saas_landing(request: Request):
    if not config.SAAS_MODE:
        return RedirectResponse("/", status_code=302)
    return templates.TemplateResponse(
        request,
        "saas_landing.html",
        _ui_context(request, title="Plutus"),
    )


@app.get("/ui/saas/login", response_class=HTMLResponse)
def ui_saas_login(request: Request):
    if not config.SAAS_MODE:
        return RedirectResponse("/", status_code=302)
    return templates.TemplateResponse(
        request, "saas_login.html", _ui_context(request, title="Sign in")
    )


@app.post("/ui/saas/login")
def ui_saas_login_post(request: Request, api_token: str = Form(...)):
    if not config.SAAS_MODE:
        return RedirectResponse("/", status_code=302)
    try:
        ctx = resolve_auth(request, form_token=api_token)
    except HTTPException:
        return templates.TemplateResponse(
            request,
            "saas_login.html",
            _ui_context(request, title="Sign in", login_error="Invalid API key or admin token"),
            status_code=401,
        )
    dest = "/ui/saas/app/admin" if ctx.is_admin else "/ui/saas/app"
    response = RedirectResponse(dest, status_code=303)
    ui_sessions.attach_session_cookie(
        response,
        is_admin=ctx.is_admin,
        tenant_id=ctx.tenant_id if ctx.tenant else None,
        api_key_id=ctx.api_key_id,
    )
    return response


@app.get("/ui/saas/signup", response_class=HTMLResponse)
def ui_saas_signup(request: Request):
    if not config.SAAS_MODE:
        return RedirectResponse("/", status_code=302)
    if not signup.signup_enabled():
        return RedirectResponse("/ui/saas/login", status_code=302)
    return templates.TemplateResponse(
        request,
        "saas_signup.html",
        _ui_context(request, 
            title="Start free trial",
            trial_days=config.SIGNUP_TRIAL_DAYS,
            trial_cap=config.SIGNUP_TRIAL_RECOMMEND_CAP,
        ),
    )


@app.post("/ui/saas/signup")
def ui_saas_signup_post(
    request: Request,
    studio_name: str = Form(...),
    email: str = Form(...),
    store_slug: str | None = Form(None),
):
    if not signup.signup_enabled():
        return RedirectResponse("/ui/saas/login", status_code=302)
    try:
        result = signup.register_studio(
            studio_name=studio_name,
            email=email,
            store_slug=store_slug,
        )
    except signup.SignupError as exc:
        return templates.TemplateResponse(
            request,
            "saas_signup.html",
            _ui_context(request, 
                title="Start free trial",
                trial_days=config.SIGNUP_TRIAL_DAYS,
                trial_cap=config.SIGNUP_TRIAL_RECOMMEND_CAP,
                signup_error=str(exc),
                studio_name=studio_name,
                email=email,
                store_slug=store_slug or "",
            ),
            status_code=400,
        )
    if result.get("verification_required"):
        return templates.TemplateResponse(
            request,
            "saas_signup_pending.html",
            _ui_context(request, 
                title="Confirm your email",
                verify_email=result.get("verify_email"),
                verify_token_hours=config.SIGNUP_VERIFY_TOKEN_HOURS,
                tenant_name=result["tenant"]["name"],
            ),
            status_code=200,
        )
    api_key = result["api_key"]
    audit.record(
        "tenant.signup",
        request=request,
        tenant_id=result["tenant"]["id"],
        resource=result["tenant"]["id"],
    )
    if config.SIGNUP_REDIRECT_BILLING and billing.billing_enabled():
        try:
            session = billing.create_checkout_session(result["tenant"]["id"])
            response = RedirectResponse(session["checkout_url"], status_code=303)
            ui_sessions.attach_session_cookie(
                response,
                is_admin=False,
                tenant_id=result["tenant"]["id"],
                api_key_id=result.get("key_id"),
            )
            return response
        except billing.BillingError:
            pass
    response = templates.TemplateResponse(
        request,
        "saas_signup_welcome.html",
        _ui_context(request, 
            title="Welcome",
            tenant=result["tenant"],
            api_key=api_key,
            store_url=result["store_url"],
            trial_days=config.SIGNUP_TRIAL_DAYS,
            trial_cap=config.SIGNUP_TRIAL_RECOMMEND_CAP,
        ),
    )
    ui_sessions.attach_session_cookie(
        response,
        is_admin=False,
        tenant_id=result["tenant"]["id"],
        api_key_id=result.get("key_id"),
    )
    return response


@app.get("/ui/saas/verify-email", response_class=HTMLResponse)
def ui_saas_verify_email(request: Request, token: str | None = Query(None)):
    from . import signup_verify

    if not token:
        return templates.TemplateResponse(
            request,
            "saas_verify_email.html",
            _ui_context(
                request,
                title="Email verification",
                verify_error="missing verification token",
            ),
            status_code=400,
        )
    try:
        result = signup_verify.verify_token(token)
    except signup_verify.SignupVerifyError as exc:
        return templates.TemplateResponse(
            request,
            "saas_verify_email.html",
            _ui_context(request, title="Email verification", verify_error=str(exc)),
            status_code=400,
        )
    response = templates.TemplateResponse(
        request,
        "saas_signup_welcome.html",
        _ui_context(request, 
            title="Welcome",
            tenant=result["tenant"],
            api_key=result["api_key"],
            store_url=result["store_url"],
            trial_days=config.SIGNUP_TRIAL_DAYS,
            trial_cap=config.SIGNUP_TRIAL_RECOMMEND_CAP,
        ),
    )
    ui_sessions.attach_session_cookie(
        response,
        is_admin=False,
        tenant_id=result["tenant"]["id"],
        api_key_id=result.get("key_id"),
    )
    audit.record(
        "tenant.signup.verified",
        request=request,
        tenant_id=result["tenant"]["id"],
        resource=result["tenant"]["id"],
    )
    return response


@app.get("/ui/saas/signup/pending", response_class=HTMLResponse)
def ui_saas_signup_pending(request: Request, email: str | None = None):
    if not signup.signup_enabled():
        return RedirectResponse("/ui/saas/login", status_code=302)
    addr = (email or request.query_params.get("email") or "").strip()
    if not addr:
        return RedirectResponse("/ui/saas/signup", status_code=302)
    return templates.TemplateResponse(
        request,
        "saas_signup_pending.html",
        _ui_context(request, 
            title="Confirm your email",
            verify_email=addr,
            verify_token_hours=config.SIGNUP_VERIFY_TOKEN_HOURS,
            resent=request.query_params.get("resent"),
        ),
    )


@app.post("/ui/saas/resend-verification")
def ui_saas_resend_verification(
    email: str = Form(...),
    return_to: str | None = Form(None),
):
    from . import signup_verify

    addr = email.strip()
    signup_verify.resend_for_email(addr)
    if return_to == "pending":
        return RedirectResponse(
            f"/ui/saas/signup/pending?email={quote_plus(addr)}&resent=1",
            status_code=303,
        )
    return RedirectResponse(
        f"/ui/saas/login?resent=1&email={quote_plus(addr)}",
        status_code=303,
    )


@app.post("/ui/logout")
def ui_logout(request: Request, csrf_token: str = Form("")):
    verify_ui_csrf(request, csrf_token)
    ui_sessions.delete_session(request.cookies.get(ui_sessions.UI_SESSION_COOKIE))
    response = RedirectResponse("/ui/saas/login", status_code=303)
    response.delete_cookie(ui_sessions.UI_SESSION_COOKIE)
    response.delete_cookie(UI_TOKEN_COOKIE)
    return response


def _tenant_ui_redirect(request: Request) -> AuthContext | RedirectResponse:
    ctx = _ui_saas_auth(request)
    if ctx is None or ctx.is_admin or not ctx.tenant:
        return RedirectResponse("/ui/saas/login", status_code=303)
    return ctx


@app.get("/ui/saas/app", response_class=HTMLResponse)
def ui_saas_tenant_app(request: Request):
    ctx = _tenant_ui_redirect(request)
    if not isinstance(ctx, AuthContext):
        return ctx
    from .metering import usage_snapshot

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
    orders_list = db.list_orders(tenant_id=ctx.tenant_id, limit=10)
    tenant = db.get_tenant(ctx.tenant_id) or ctx.tenant
    return templates.TemplateResponse(
        request,
        "saas_dashboard.html",
        _ui_context(request, 
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
        ),
    )


@app.post("/ui/saas/app/settings")
def ui_saas_tenant_settings(
    request: Request,
    notify_email: str | None = Form(None),
):
    ctx = _tenant_ui_redirect(request)
    if not isinstance(ctx, AuthContext):
        return ctx
    addr = (notify_email or "").strip().lower()
    if addr and ("@" not in addr or "." not in addr.split("@")[-1]):
        return RedirectResponse(
            "/ui/saas/app?settings_error=invalid+email",
            status_code=303,
        )
    db.update_tenant(ctx.tenant_id, notify_email=addr or None)
    audit.record("tenant.settings.notify_email", request=request, ctx=ctx)
    return RedirectResponse("/ui/saas/app?settings_saved=1", status_code=303)


def _admin_ui_redirect(request: Request) -> AuthContext | RedirectResponse:
    ctx = _ui_saas_auth(request)
    if ctx is None or not ctx.is_admin:
        return RedirectResponse("/ui/saas/login", status_code=303)
    return ctx


@app.get("/ui/saas/app/admin", response_class=HTMLResponse)
def ui_saas_admin_app(request: Request):
    ctx = _admin_ui_redirect(request)
    if not isinstance(ctx, AuthContext):
        return ctx
    return templates.TemplateResponse(
        request,
        "saas_dashboard.html",
        _ui_context(request, 
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


@app.post("/ui/saas/app/admin/tenants")
def ui_saas_admin_create_tenant(
    request: Request,
    tenant_id: str = Form(...),
    name: str = Form(...),
    store_slug: str | None = Form(None),
    monthly_recommend_cap: str | None = Form(None),
):
    ctx = _admin_ui_redirect(request)
    if not isinstance(ctx, AuthContext):
        return ctx
    cap = (
        int(monthly_recommend_cap)
        if monthly_recommend_cap and monthly_recommend_cap.strip()
        else None
    )
    from . import tenants

    try:
        tenant = tenants.create_tenant(
            tenant_id,
            name=name,
            store_slug=store_slug,
            monthly_recommend_cap=cap,
        )
    except TenantError as exc:
        return RedirectResponse(f"/ui/saas/app/admin?error={quote_plus(str(exc))}", status_code=303)
    db.update_tenant(tenant["id"], email_verified_at=datetime.now(UTC).isoformat())
    audit.record("admin.tenant.create", request=request, ctx=ctx, tenant_id=tenant["id"])
    issued = tenants.issue_api_key(tenant["id"], label="bootstrap")
    return templates.TemplateResponse(
        request,
        "saas_admin_tenant.html",
        _admin_tenant_context(
            request,
            tenant["id"],
            admin_message="Tenant created.",
            issued_api_key=issued["api_key"],
        ),
    )


def _admin_tenant_context(
    request: Request,
    tenant_id: str,
    *,
    admin_message: str | None = None,
    admin_error: str | None = None,
    issued_api_key: str | None = None,
) -> dict:
    from .metering import usage_snapshot

    tenant = db.get_tenant(tenant_id)
    if not tenant:
        raise HTTPException(status_code=404, detail="tenant not found")
    return _ui_context(request, 
        title=f"Tenant {tenant_id}",
        tenant=tenant,
        usage=usage_snapshot(tenant_id),
        keys=db.list_tenant_keys(tenant_id),
        orders=db.list_orders(tenant_id=tenant_id, limit=15),
        admin_message=admin_message,
        admin_error=admin_error,
        issued_api_key=issued_api_key,
    )


@app.get("/ui/saas/app/admin/tenants/{tenant_id}", response_class=HTMLResponse)
def ui_saas_admin_tenant(
    request: Request,
    tenant_id: str,
    updated: str | None = Query(None),
    revoked: str | None = Query(None),
    error: str | None = Query(None),
):
    ctx = _admin_ui_redirect(request)
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
        _admin_tenant_context(
            request,
            tenant_id,
            admin_message=admin_message,
            admin_error=error,
        ),
    )


@app.post("/ui/saas/app/admin/tenants/{tenant_id}")
def ui_saas_admin_patch_tenant(
    request: Request,
    tenant_id: str,
    name: str | None = Form(None),
    store_slug: str | None = Form(None),
    notify_email: str | None = Form(None),
    active: str = Form("1"),
    monthly_recommend_cap: str | None = Form(None),
):
    ctx = _admin_ui_redirect(request)
    if not isinstance(ctx, AuthContext):
        return ctx
    if not db.get_tenant(tenant_id):
        return RedirectResponse("/ui/saas/app/admin?error=tenant+not+found", status_code=303)
    fields: dict = {"active": active.strip() in {"1", "true", "yes", "on"}}
    if name and name.strip():
        fields["name"] = name.strip()
    if store_slug and store_slug.strip():
        slug = store_slug.strip().lower()
        existing = db.get_tenant_by_slug(slug)
        if existing and existing["id"] != tenant_id:
            return RedirectResponse(
                f"/ui/saas/app/admin/tenants/{tenant_id}?"
                f"error={quote_plus(f'store slug already taken: {slug}')}",
                status_code=303,
            )
        fields["store_slug"] = slug
    if notify_email is not None:
        fields["notify_email"] = notify_email.strip() or None
    if monthly_recommend_cap is not None:
        stripped = monthly_recommend_cap.strip()
        if stripped:
            try:
                fields["monthly_recommend_cap"] = int(stripped)
            except ValueError:
                return RedirectResponse(
                    f"/ui/saas/app/admin/tenants/{tenant_id}?"
                    "error=monthly+recommend+cap+must+be+a+number",
                    status_code=303,
                )
        else:
            fields["monthly_recommend_cap"] = None
    db.update_tenant(tenant_id, **fields)
    audit.record("admin.tenant.patch", request=request, ctx=ctx, tenant_id=tenant_id, detail=fields)
    return RedirectResponse(f"/ui/saas/app/admin/tenants/{tenant_id}?updated=1", status_code=303)


@app.post("/ui/saas/app/admin/tenants/{tenant_id}/keys")
def ui_saas_admin_issue_key(
    request: Request,
    tenant_id: str,
    label: str | None = Form(None),
):
    from . import tenants

    ctx = _admin_ui_redirect(request)
    if not isinstance(ctx, AuthContext):
        return ctx
    if not db.get_tenant(tenant_id):
        return RedirectResponse("/ui/saas/app/admin?error=tenant+not+found", status_code=303)
    try:
        issued = tenants.issue_api_key(tenant_id, label=label.strip() if label else None)
    except TenantError as exc:
        return templates.TemplateResponse(
            request,
            "saas_admin_tenant.html",
            _admin_tenant_context(request, tenant_id, admin_error=str(exc)),
            status_code=400,
        )
    audit.record(
        "admin.tenant.key.issue",
        request=request,
        ctx=ctx,
        tenant_id=tenant_id,
        resource=issued["key_id"],
    )
    return templates.TemplateResponse(
        request,
        "saas_admin_tenant.html",
        _admin_tenant_context(request, tenant_id, issued_api_key=issued["api_key"]),
    )


@app.post("/ui/saas/app/admin/tenants/{tenant_id}/keys/{key_id}/revoke")
def ui_saas_admin_revoke_key(request: Request, tenant_id: str, key_id: str):
    from . import tenants

    ctx = _admin_ui_redirect(request)
    if not isinstance(ctx, AuthContext):
        return ctx
    if not db.get_tenant(tenant_id):
        return RedirectResponse("/ui/saas/app/admin?error=tenant+not+found", status_code=303)
    if key_id not in {row["id"] for row in db.list_tenant_keys(tenant_id)}:
        return RedirectResponse(
            f"/ui/saas/app/admin/tenants/{tenant_id}?error=key+not+found",
            status_code=303,
        )
    tenants.revoke_key(key_id)
    audit.record(
        "admin.tenant.key.revoke",
        request=request,
        ctx=ctx,
        tenant_id=tenant_id,
        resource=key_id,
    )
    return RedirectResponse(f"/ui/saas/app/admin/tenants/{tenant_id}?revoked=1", status_code=303)


@app.post("/ui/saas/app/admin/tenants/{tenant_id}/billing/checkout")
def ui_saas_admin_billing_checkout(request: Request, tenant_id: str):
    ctx = _admin_ui_redirect(request)
    if not isinstance(ctx, AuthContext):
        return ctx
    if not db.get_tenant(tenant_id):
        return RedirectResponse("/ui/saas/app/admin?error=tenant+not+found", status_code=303)
    try:
        session = billing.create_checkout_session(tenant_id)
    except billing.BillingError as exc:
        return templates.TemplateResponse(
            request,
            "saas_admin_tenant.html",
            _admin_tenant_context(request, tenant_id, admin_error=str(exc)),
            status_code=400,
        )
    audit.record("billing.checkout", request=request, ctx=ctx, tenant_id=tenant_id, detail=session)
    return RedirectResponse(session["checkout_url"], status_code=303)


@app.get("/ui/saas/app/sell", response_class=HTMLResponse)
def ui_saas_sell(
    request: Request,
    run_id: int | None = Query(None),
    analyzing: str | None = Query(None),
    published: str | None = Query(None),
    offer_url: str | None = Query(None),
    auto: str | None = Query(None),
):
    ctx = _tenant_ui_redirect(request)
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
        _ui_context(request, 
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


@app.post("/ui/saas/app/sell")
async def ui_saas_sell_post(
    request: Request,
    action: str = Form("upload_publish"),
    gallery_name: str | None = Form(None),
    files: list[UploadFile] | None = File(None),
    run_id: int | None = Form(None),
    batch_id: str | None = Form(None),
    label: str | None = Form(None),
    argus_run_id: int | None = Form(None),
):
    ctx = _tenant_ui_redirect(request)
    if not isinstance(ctx, AuthContext):
        return ctx
    offer_label = label.strip() if label and label.strip() else None

    def _sell_redirect(result: dict) -> RedirectResponse:
        audit.record(
            "tenant.sell.publish",
            request=request,
            ctx=ctx,
            resource=str(result.get("run_id")),
            detail={"offer_token": result.get("offer_token")},
        )
        metrics.inc_tenant(ctx.tenant_id, "sell_publish")
        return RedirectResponse(
            "/ui/saas/app/sell?"
            f"published=1&run_id={result['run_id']}"
            f"&offer_url={quote_plus(result['offer_url'])}",
            status_code=303,
        )

    def _sell_error_redirect(message: str) -> RedirectResponse:
        return RedirectResponse(
            f"/ui/saas/app/sell?error={quote_plus(message)}",
            status_code=303,
        )

    try:
        if action in {"publish_run", "publish_batch"}:
            result = sell.publish_and_sell(
                ctx.tenant_id,
                run_id=run_id,
                batch_id=batch_id,
                label=offer_label,
            )
            return _sell_redirect(result)

        if action == "upload_publish":
            if not gallery_name or not files:
                return _sell_error_redirect("gallery name and photos are required")
            batch = uploads.create_batch(tenant_id=ctx.tenant_id, name=gallery_name)
            new_batch_id = batch["id"]
            payload: list[tuple[str, bytes]] = []
            for f in files:
                if not f.filename:
                    continue
                payload.append((f.filename, await f.read()))
            try:
                uploads.add_files(tenant_id=ctx.tenant_id, batch_id=new_batch_id, files=payload)
            except uploads.UploadError as exc:
                return _sell_error_redirect(str(exc))
            audit.record(
                "tenant.sell.upload",
                request=request,
                ctx=ctx,
                resource=new_batch_id,
                detail={"photos": batch.get("photo_count")},
            )
            try:
                analyze_result = service.analyze_upload_batch(
                    new_batch_id,
                    tenant_id=ctx.tenant_id,
                    name=gallery_name,
                    argus_run_id=argus_run_id,
                )
            except (MeteringError, service.RecommendError, uploads.UploadError) as exc:
                return _sell_error_redirect(str(exc))
            if analyze_result.get("queued"):
                return RedirectResponse(
                    f"/ui/saas/app/sell?analyzing={new_batch_id}&auto=1",
                    status_code=303,
                )
            metrics.inc_tenant(ctx.tenant_id, "recommend_upload")
            result = sell.publish_and_sell(
                ctx.tenant_id,
                run_id=int(analyze_result["run_id"]),
                label=offer_label,
            )
            return _sell_redirect(result)

        return _sell_error_redirect(f"unknown action: {action}")
    except SellError as exc:
        if batch_id or run_id:
            qs = f"run_id={run_id}" if run_id else f"analyzing={batch_id}"
            return RedirectResponse(
                f"/ui/saas/app/sell?{qs}&error={quote_plus(exc.message)}",
                status_code=303,
            )
        return _sell_error_redirect(exc.message)


@app.get("/ui/saas/app/upload", response_class=HTMLResponse)
def ui_saas_upload(
    request: Request,
    uploaded: str | None = Query(None),
    analyzing: str | None = Query(None),
):
    ctx = _tenant_ui_redirect(request)
    if not isinstance(ctx, AuthContext):
        return ctx
    return templates.TemplateResponse(
        request,
        "saas_upload.html",
        _ui_context(request, 
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


@app.post("/ui/saas/app/upload")
async def ui_saas_upload_post(
    request: Request,
    gallery_name: str = Form(...),
    files: list[UploadFile] = File(...),
    analyze: str | None = Form(None),
    argus_run_id: int | None = Form(None),
):
    ctx = _tenant_ui_redirect(request)
    if not isinstance(ctx, AuthContext):
        return ctx
    batch = uploads.create_batch(tenant_id=ctx.tenant_id, name=gallery_name)
    batch_id = batch["id"]
    payload: list[tuple[str, bytes]] = []
    for f in files:
        if not f.filename:
            continue
        payload.append((f.filename, await f.read()))
    try:
        uploads.add_files(tenant_id=ctx.tenant_id, batch_id=batch_id, files=payload)
    except uploads.UploadError as exc:
        return templates.TemplateResponse(
            request,
            "saas_upload.html",
            _ui_context(request, 
                title="Upload gallery",
                tenant=ctx.tenant,
                batches=db.list_upload_batches(tenant_id=ctx.tenant_id, limit=10),
                max_files=config.MAX_UPLOAD_FILES,
                max_mb=config.MAX_UPLOAD_FILE_BYTES // (1024 * 1024),
                upload_error=str(exc),
            ),
            status_code=400,
        )
    audit.record(
        "tenant.upload",
        request=request,
        ctx=ctx,
        resource=batch_id,
        detail={"photos": batch.get("photo_count")},
    )
    if analyze:
        try:
            result = service.analyze_upload_batch(
                batch_id,
                tenant_id=ctx.tenant_id,
                name=gallery_name,
                argus_run_id=argus_run_id,
            )
        except (MeteringError, service.RecommendError, uploads.UploadError) as exc:
            return RedirectResponse(
                f"/ui/saas/app/upload?error={quote_plus(str(exc))}",
                status_code=303,
            )
        if result.get("queued"):
            return RedirectResponse(
                f"/ui/saas/app/upload?analyzing={batch_id}",
                status_code=303,
            )
        metrics.inc_tenant(ctx.tenant_id, "recommend_upload")
        return RedirectResponse(f"/runs/{result['run_id']}", status_code=303)
    return RedirectResponse("/ui/saas/app/upload?uploaded=1", status_code=303)


@app.post("/ui/saas/app/upload/{batch_id}/analyze")
def ui_saas_upload_analyze(
    request: Request,
    batch_id: str,
    argus_run_id: int | None = Form(None),
):
    ctx = _tenant_ui_redirect(request)
    if not isinstance(ctx, AuthContext):
        return ctx
    try:
        result = service.analyze_upload_batch(
            batch_id,
            tenant_id=ctx.tenant_id,
            argus_run_id=argus_run_id,
        )
    except (MeteringError, service.RecommendError, uploads.UploadError) as exc:
        return RedirectResponse(
            f"/ui/saas/app/upload?error={quote_plus(str(exc))}",
            status_code=303,
        )
    if result.get("queued"):
        return RedirectResponse(
            f"/ui/saas/app/upload?analyzing={batch_id}",
            status_code=303,
        )
    metrics.inc_tenant(ctx.tenant_id, "recommend_upload")
    return RedirectResponse(f"/runs/{result['run_id']}", status_code=303)


@app.get("/api/mise/galleries", response_class=JSONResponse)
def api_mise_galleries(
    published: bool | None = Query(None),
    ctx: AuthContext = Depends(require_bearer),
) -> JSONResponse:
    from . import mise_client

    if not mise_client.is_enabled():
        raise HTTPException(status_code=503, detail="Mise API is not configured")
    try:
        body = mise_client.list_galleries(published=published)
    except mise_client.MiseClientError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return JSONResponse(body)


@app.get("/ui/saas/app/mise", response_class=HTMLResponse)
def ui_saas_mise_galleries(request: Request):
    from . import mise_client

    ctx = _tenant_ui_redirect(request)
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
        _ui_context(request, 
            title="Mise galleries",
            tenant=ctx.tenant,
            galleries=galleries,
            mise_message="Bundles generated — publish an offer from Publish & sell."
            if request.query_params.get("recommended")
            else None,
            mise_run_id=request.query_params.get("run_id"),
            mise_error=mise_error or request.query_params.get("error"),
        ),
    )


@app.post("/ui/saas/app/mise/{gallery_id}/recommend")
def ui_saas_mise_recommend(
    request: Request,
    gallery_id: int,
    argus_run_id: int | None = Form(None),
):
    ctx = _tenant_ui_redirect(request)
    if not isinstance(ctx, AuthContext):
        return ctx
    try:
        result = service.analyze_mise_gallery(
            gallery_id,
            argus_run_id=argus_run_id,
            tenant_id=ctx.tenant_id,
        )
    except MeteringError as exc:
        return RedirectResponse(
            f"/ui/saas/app/mise?error={quote_plus(str(exc))}",
            status_code=303,
        )
    except service.RecommendError as exc:
        return RedirectResponse(
            f"/ui/saas/app/mise?error={quote_plus(str(exc))}",
            status_code=303,
        )
    except FileNotFoundError as exc:
        return RedirectResponse(
            f"/ui/saas/app/mise?error={quote_plus(str(exc))}",
            status_code=303,
        )
    metrics.inc_tenant(ctx.tenant_id, "recommend_mise")
    audit.record(
        "recommend.mise",
        request=request,
        ctx=ctx,
        resource=str(result["run_id"]),
        detail={"mise_gallery_id": gallery_id},
    )
    return RedirectResponse(
        f"/ui/saas/app/mise?recommended=1&run_id={result['run_id']}",
        status_code=303,
    )


@app.get("/ui/saas/app/catalog", response_class=HTMLResponse)
def ui_saas_tenant_catalog(request: Request, saved: str | None = Query(None)):
    ctx = _tenant_ui_redirect(request)
    if not isinstance(ctx, AuthContext):
        return ctx
    return templates.TemplateResponse(
        request,
        "saas_catalog.html",
        _ui_context(request, 
            title="Product pricing",
            tenant=ctx.tenant,
            products=catalog.list_catalog(ctx.tenant_id),
            catalog_message="Pricing saved." if saved else None,
        ),
    )


@app.post("/ui/saas/app/catalog")
async def ui_saas_tenant_catalog_save(request: Request):
    ctx = _tenant_ui_redirect(request)
    if not isinstance(ctx, AuthContext):
        return ctx
    form = await request.form()
    for product in catalog.PRODUCTS:
        sku = product.sku
        cents_raw = form.get(f"cents_{sku}")
        label_raw = form.get(f"label_{sku}")
        active = f"active_{sku}" in form
        has_cents = bool(cents_raw and str(cents_raw).strip())
        has_label = bool(label_raw and str(label_raw).strip())
        if not has_cents and not has_label and active:
            db.delete_product_override(ctx.tenant_id, sku)
            continue
        unit_cents = int(str(cents_raw).strip()) if has_cents else product.unit_cents
        label = str(label_raw).strip() if has_label else None
        db.upsert_product_override(
            ctx.tenant_id,
            sku,
            unit_cents=unit_cents,
            label=label,
            active=active,
        )
    audit.record("tenant.catalog.save", request=request, ctx=ctx)
    return RedirectResponse("/ui/saas/app/catalog?saved=1", status_code=303)


@app.get("/ui/saas/app/orders/{order_id}", response_class=HTMLResponse)
def ui_saas_order_detail(request: Request, order_id: int):
    ctx = _ui_saas_auth(request)
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
    return templates.TemplateResponse(
        request,
        "saas_order.html",
        _ui_context(request, 
            title=f"Order {order_id}",
            order=order,
            tenant=tenant,
            run=run,
            is_admin=ctx.is_admin,
            fulfillment_events=db.list_fulfillment_events(order_id),
        ),
    )


@app.post("/ui/saas/app/keys")
def ui_saas_tenant_issue_key(request: Request, label: str | None = Form(None)):
    from . import tenants

    ctx = _tenant_ui_redirect(request)
    if not isinstance(ctx, AuthContext):
        return ctx
    try:
        issued = tenants.issue_api_key(ctx.tenant_id, label=label.strip() if label else None)
    except TenantError as exc:
        return RedirectResponse(f"/ui/saas/app?keys_error={quote_plus(str(exc))}", status_code=303)
    audit.record("tenant.key.issue", request=request, ctx=ctx, resource=issued["key_id"])
    from .metering import usage_snapshot

    return templates.TemplateResponse(
        request,
        "saas_dashboard.html",
        _ui_context(request, 
            title="Dashboard",
            portal_mode="tenant",
            tenant=ctx.tenant,
            usage=usage_snapshot(ctx.tenant_id),
            recent_runs=db.list_runs(limit=10, tenant_id=ctx.tenant_id),
            tenant_keys=[
                {**dict(row), "is_current": row["id"] == ctx.api_key_id}
                for row in db.list_tenant_keys(ctx.tenant_id)
            ],
            orders=db.list_orders(tenant_id=ctx.tenant_id, limit=10),
            issued_api_key=issued["api_key"],
        ),
    )


@app.post("/ui/saas/app/keys/{key_id}/revoke")
def ui_saas_tenant_revoke_key(request: Request, key_id: str):
    from . import tenants

    ctx = _tenant_ui_redirect(request)
    if not isinstance(ctx, AuthContext):
        return ctx
    active = {row["id"] for row in db.list_tenant_keys(ctx.tenant_id) if not row.get("revoked_at")}
    if key_id not in active:
        return RedirectResponse("/ui/saas/app?keys_error=key+not+found", status_code=303)
    if key_id == ctx.api_key_id:
        return RedirectResponse(
            "/ui/saas/app?keys_error=cannot+revoke+the+key+used+for+this+session",
            status_code=303,
        )
    if len(active) <= 1:
        return RedirectResponse(
            "/ui/saas/app?keys_error=cannot+revoke+your+only+active+key",
            status_code=303,
        )
    tenants.revoke_key(key_id)
    audit.record("tenant.key.revoke", request=request, ctx=ctx, resource=key_id)
    return RedirectResponse("/ui/saas/app?keys_updated=1", status_code=303)


@app.post("/ui/homelab/share-link")
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


@app.get("/ui/homelab/orders/{order_id}", response_class=HTMLResponse)
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
        _ui_context(request, 
            title=f"Order {order_id}",
            order=order,
            tenant=tenant,
            run=run,
            is_admin=False,
            fulfillment_events=db.list_fulfillment_events(order_id),
        ),
    )


@app.post("/ui/saas/app/share-link")
def ui_saas_create_share_link(
    request: Request,
    run_id: int = Form(...),
    label: str | None = Form(None),
):
    ctx = _tenant_ui_redirect(request)
    if not isinstance(ctx, AuthContext):
        return ctx
    try:
        link = create_share_link(
            tenant_id=ctx.tenant_id,
            run_id=run_id,
            label=label.strip() if label else None,
        )
    except StorefrontError as exc:
        return RedirectResponse(
            f"/runs/{run_id}?share_error={quote_plus(str(exc))}",
            status_code=303,
        )
    audit.record("storefront.link.create", request=request, ctx=ctx, resource=link["token"])
    return RedirectResponse(
        f"/runs/{run_id}?share_created=1&offer_url={quote_plus(link['public_url'])}",
        status_code=303,
    )


@app.get("/ui/saas/billing", response_class=HTMLResponse)
def ui_saas_billing(
    request: Request,
    success: str | None = Query(None),
    cancelled: str | None = Query(None),
):
    ctx = _tenant_ui_redirect(request)
    if not isinstance(ctx, AuthContext):
        return ctx
    from .metering import usage_snapshot

    usage = usage_snapshot(ctx.tenant_id)
    return templates.TemplateResponse(
        request,
        "saas_billing.html",
        _ui_context(request, 
            title="Billing",
            tenant=ctx.tenant,
            usage=usage,
            subscription=billing.tenant_subscription_view(ctx.tenant),
            billing_success=bool(success),
            billing_cancelled=bool(cancelled),
            billing_info=billing.billing_status(),
        ),
    )


@app.post("/ui/saas/billing/checkout")
def ui_saas_billing_checkout(request: Request):
    ctx = _tenant_ui_redirect(request)
    if not isinstance(ctx, AuthContext):
        return ctx
    try:
        session = billing.create_checkout_session(ctx.tenant_id)
    except billing.BillingError as exc:
        from .metering import usage_snapshot

        return templates.TemplateResponse(
            request,
            "saas_billing.html",
            _ui_context(request, 
                title="Billing",
                tenant=ctx.tenant,
                usage=usage_snapshot(ctx.tenant_id),
                subscription=billing.tenant_subscription_view(ctx.tenant),
                billing_error=str(exc),
                billing_info=billing.billing_status(),
            ),
            status_code=400,
        )
    audit.record("billing.checkout", request=request, ctx=ctx, detail=session)
    return RedirectResponse(session["checkout_url"], status_code=303)


@app.post("/ui/saas/billing/portal")
def ui_saas_billing_portal(request: Request):
    ctx = _tenant_ui_redirect(request)
    if not isinstance(ctx, AuthContext):
        return ctx
    try:
        portal = billing.create_billing_portal_session(ctx.tenant_id)
    except billing.BillingError as exc:
        from .metering import usage_snapshot

        return templates.TemplateResponse(
            request,
            "saas_billing.html",
            _ui_context(request, 
                title="Billing",
                tenant=ctx.tenant,
                usage=usage_snapshot(ctx.tenant_id),
                subscription=billing.tenant_subscription_view(ctx.tenant),
                billing_error=str(exc),
                billing_info=billing.billing_status(),
            ),
            status_code=400,
        )
    return RedirectResponse(portal["portal_url"], status_code=303)


def main() -> None:
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=config.HOST,
        port=config.PORT,
        reload=False,
    )


if __name__ == "__main__":
    main()