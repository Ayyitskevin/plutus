from __future__ import annotations

import logging
from datetime import UTC, datetime
from urllib.parse import quote_plus

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import RedirectResponse

from .. import (
    audit,
    billing,
    catalog,
    config,
    db,
    metrics,
    sell,
    service,
    ui_sessions,
    uploads,
)
from ..auth import UI_TOKEN_COOKIE
from ..auth_context import AuthContext
from ..metering import MeteringError
from ..sell import SellError
from ..storefront import StorefrontError, create_share_link
from ..tenants import TenantError
from .csrf import require_csrf
from .deps import (
    admin_tenant_context,
    admin_ui_redirect,
    templates,
    tenant_ui_redirect,
    ui_context,
)

log = logging.getLogger("plutus")

router = APIRouter(dependencies=[Depends(require_csrf)])

@router.post("/ui/logout")
def ui_logout(request: Request):
    ui_sessions.delete_session(request.cookies.get(ui_sessions.UI_SESSION_COOKIE))
    response = RedirectResponse("/ui/saas/login", status_code=303)
    response.delete_cookie(ui_sessions.UI_SESSION_COOKIE)
    response.delete_cookie(UI_TOKEN_COOKIE)
    return response



@router.post("/ui/saas/app/settings")
def ui_saas_tenant_settings(
    request: Request,
    notify_email: str | None = Form(None),
):
    ctx = tenant_ui_redirect(request)
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



@router.post("/ui/saas/app/admin/tenants")
def ui_saas_admin_create_tenant(
    request: Request,
    tenant_id: str = Form(...),
    name: str = Form(...),
    store_slug: str | None = Form(None),
    monthly_recommend_cap: str | None = Form(None),
):
    ctx = admin_ui_redirect(request)
    if not isinstance(ctx, AuthContext):
        return ctx
    cap = (
        int(monthly_recommend_cap)
        if monthly_recommend_cap and monthly_recommend_cap.strip()
        else None
    )
    from .. import tenants

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
        admin_tenant_context(
            request,
            tenant["id"],
            admin_message="Tenant created.",
            issued_api_key=issued["api_key"],
        ),
    )



@router.post("/ui/saas/app/admin/tenants/{tenant_id}")
def ui_saas_admin_patch_tenant(
    request: Request,
    tenant_id: str,
    name: str | None = Form(None),
    store_slug: str | None = Form(None),
    notify_email: str | None = Form(None),
    active: str = Form("1"),
    monthly_recommend_cap: str | None = Form(None),
):
    ctx = admin_ui_redirect(request)
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



@router.post("/ui/saas/app/admin/tenants/{tenant_id}/keys")
def ui_saas_admin_issue_key(
    request: Request,
    tenant_id: str,
    label: str | None = Form(None),
):
    from .. import tenants

    ctx = admin_ui_redirect(request)
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
            admin_tenant_context(request, tenant_id, admin_error=str(exc)),
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
        admin_tenant_context(request, tenant_id, issued_api_key=issued["api_key"]),
    )



@router.post("/ui/saas/app/admin/tenants/{tenant_id}/keys/{key_id}/revoke")
def ui_saas_admin_revoke_key(request: Request, tenant_id: str, key_id: str):
    from .. import tenants

    ctx = admin_ui_redirect(request)
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



@router.post("/ui/saas/app/admin/tenants/{tenant_id}/billing/checkout")
def ui_saas_admin_billing_checkout(request: Request, tenant_id: str):
    ctx = admin_ui_redirect(request)
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
            admin_tenant_context(request, tenant_id, admin_error=str(exc)),
            status_code=400,
        )
    audit.record("billing.checkout", request=request, ctx=ctx, tenant_id=tenant_id, detail=session)
    return RedirectResponse(session["checkout_url"], status_code=303)



@router.post("/ui/saas/app/sell")
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
    ctx = tenant_ui_redirect(request)
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



@router.post("/ui/saas/app/upload")
async def ui_saas_upload_post(
    request: Request,
    gallery_name: str = Form(...),
    files: list[UploadFile] = File(...),
    analyze: str | None = Form(None),
    argus_run_id: int | None = Form(None),
):
    ctx = tenant_ui_redirect(request)
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
            ui_context(request, 
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



@router.post("/ui/saas/app/upload/{batch_id}/analyze")
def ui_saas_upload_analyze(
    request: Request,
    batch_id: str,
    argus_run_id: int | None = Form(None),
):
    ctx = tenant_ui_redirect(request)
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



@router.post("/ui/saas/app/mise/{gallery_id}/recommend")
def ui_saas_mise_recommend(
    request: Request,
    gallery_id: int,
    argus_run_id: int | None = Form(None),
):
    ctx = tenant_ui_redirect(request)
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



@router.post("/ui/saas/app/catalog")
async def ui_saas_tenant_catalog_save(request: Request):
    ctx = tenant_ui_redirect(request)
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



@router.post("/ui/saas/app/keys")
def ui_saas_tenant_issue_key(request: Request, label: str | None = Form(None)):
    from .. import tenants

    ctx = tenant_ui_redirect(request)
    if not isinstance(ctx, AuthContext):
        return ctx
    try:
        issued = tenants.issue_api_key(ctx.tenant_id, label=label.strip() if label else None)
    except TenantError as exc:
        return RedirectResponse(f"/ui/saas/app?keys_error={quote_plus(str(exc))}", status_code=303)
    audit.record("tenant.key.issue", request=request, ctx=ctx, resource=issued["key_id"])
    from ..metering import usage_snapshot

    return templates.TemplateResponse(
        request,
        "saas_dashboard.html",
        ui_context(request, 
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



@router.post("/ui/saas/app/keys/{key_id}/revoke")
def ui_saas_tenant_revoke_key(request: Request, key_id: str):
    from .. import tenants

    ctx = tenant_ui_redirect(request)
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



@router.post("/ui/saas/app/share-link")
def ui_saas_create_share_link(
    request: Request,
    run_id: int = Form(...),
    label: str | None = Form(None),
):
    ctx = tenant_ui_redirect(request)
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



@router.post("/ui/saas/billing/checkout")
def ui_saas_billing_checkout(request: Request):
    ctx = tenant_ui_redirect(request)
    if not isinstance(ctx, AuthContext):
        return ctx
    try:
        session = billing.create_checkout_session(ctx.tenant_id)
    except billing.BillingError as exc:
        from ..metering import usage_snapshot

        return templates.TemplateResponse(
            request,
            "saas_billing.html",
            ui_context(request, 
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



@router.post("/ui/saas/billing/portal")
def ui_saas_billing_portal(request: Request):
    ctx = tenant_ui_redirect(request)
    if not isinstance(ctx, AuthContext):
        return ctx
    try:
        portal = billing.create_billing_portal_session(ctx.tenant_id)
    except billing.BillingError as exc:
        from ..metering import usage_snapshot

        return templates.TemplateResponse(
            request,
            "saas_billing.html",
            ui_context(request, 
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


