from __future__ import annotations

import logging
from datetime import UTC, datetime
from urllib.parse import quote_plus

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse

from .. import (
    audit,
    billing,
    catalog,
    config,
    db,
    metrics,
    notifications,
    sell,
    service,
    ui_sessions,
    uploads,
)
from ..async_io import run_sync
from ..auth_context import AuthContext
from ..bundle_editor import BundleEditError, parse_bundle_form, save_run_edits
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
    ui_saas_auth,
)

log = logging.getLogger("plutus")

router = APIRouter(dependencies=[Depends(require_csrf)])

@router.post("/ui/logout")
def ui_logout(request: Request):
    ui_sessions.delete_session(request.cookies.get(ui_sessions.UI_SESSION_COOKIE))
    response = RedirectResponse("/ui/saas/login", status_code=303)
    response.delete_cookie(ui_sessions.UI_SESSION_COOKIE)
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


@router.post("/ui/saas/app/notifications/test")
def ui_saas_notifications_test(request: Request):
    ctx = tenant_ui_redirect(request)
    if not isinstance(ctx, AuthContext):
        return ctx
    tenant = db.get_tenant(ctx.tenant_id) or ctx.tenant
    addr = (tenant.get("notify_email") or "").strip().lower()
    if not addr:
        return RedirectResponse(
            "/ui/saas/app?notification_test_error=save+a+notify+email+first",
            status_code=303,
        )
    if not notifications.smtp_ready():
        return RedirectResponse(
            "/ui/saas/app?notification_test_error=smtp+not+configured+on+server",
            status_code=303,
        )
    sent = notifications.send_test_email(to=addr, tenant=tenant)
    audit.record("tenant.notifications.test", request=request, ctx=ctx)
    if not sent:
        return RedirectResponse(
            "/ui/saas/app?notification_test_error=delivery+failed",
            status_code=303,
        )
    return RedirectResponse("/ui/saas/app?notification_test_sent=1", status_code=303)


@router.post("/ui/saas/app/orders/{order_id}/poll-lab")
def ui_saas_order_poll_lab(request: Request, order_id: int):
    ctx = ui_saas_auth(request)
    if ctx is None:
        return RedirectResponse("/ui/saas/login", status_code=303)
    tenant_id = None if ctx.is_admin else ctx.tenant_id
    order = db.get_order(order_id, tenant_id=tenant_id)
    if not order:
        return RedirectResponse("/ui/saas/app?order_error=order+not+found", status_code=303)
    from .. import lab

    try:
        lab.poll_order(order_id)
    except lab.LabError:
        return RedirectResponse(
            f"/ui/saas/app/orders/{order_id}?order_error=lab+poll+failed",
            status_code=303,
        )
    audit.record("order.lab.poll", request=request, ctx=ctx, resource=str(order_id))
    return RedirectResponse(f"/ui/saas/app/orders/{order_id}?lab_polled=1", status_code=303)


@router.post("/ui/saas/app/orders/{order_id}/resend-confirmation")
def ui_saas_order_resend_confirmation(request: Request, order_id: int):
    ctx = ui_saas_auth(request)
    if ctx is None:
        return RedirectResponse("/ui/saas/login", status_code=303)
    tenant_id = None if ctx.is_admin else ctx.tenant_id
    order = db.get_order(order_id, tenant_id=tenant_id)
    if not order:
        return RedirectResponse("/ui/saas/app?order_error=order+not+found", status_code=303)
    if not notifications.smtp_ready():
        return RedirectResponse(
            f"/ui/saas/app/orders/{order_id}?order_error=smtp+not+configured",
            status_code=303,
        )
    if not order.get("client_email"):
        return RedirectResponse(
            f"/ui/saas/app/orders/{order_id}?order_error=no+client+email",
            status_code=303,
        )
    sent = notifications.resend_client_confirmation(order_id)
    audit.record("order.client.resend", request=request, ctx=ctx, resource=str(order_id))
    if not sent:
        return RedirectResponse(
            f"/ui/saas/app/orders/{order_id}?order_error=resend+failed",
            status_code=303,
        )
    return RedirectResponse(f"/ui/saas/app/orders/{order_id}?resent=1", status_code=303)


@router.post("/ui/saas/app/admin/tenants")
def ui_saas_admin_create_tenant(
    request: Request,
    tenant_id: str = Form(...),
    name: str = Form(...),
    store_slug: str | None = Form(None),
    notify_email: str | None = Form(None),
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
    addr = (notify_email or "").strip().lower()
    fields: dict = {"email_verified_at": datetime.now(UTC).isoformat()}
    if addr:
        fields["notify_email"] = addr
    db.update_tenant(tenant["id"], **fields)
    tenant = db.get_tenant(tenant["id"]) or tenant
    audit.record("admin.tenant.create", request=request, ctx=ctx, tenant_id=tenant["id"])
    issued = tenants.issue_api_key(tenant["id"], label="bootstrap")
    welcome_sent = False
    if addr and notifications.smtp_ready():
        welcome_sent = notifications.send_tenant_welcome_email(
            to=addr,
            tenant=tenant,
            api_key=issued["api_key"],
        )
    if welcome_sent:
        admin_message = f"Tenant created. Welcome email sent to {addr}."
    elif addr and not notifications.smtp_ready():
        admin_message = (
            "Tenant created. SMTP not configured — share the API key manually."
        )
    else:
        admin_message = "Tenant created."
    return templates.TemplateResponse(
        request,
        "saas_admin_tenant.html",
        admin_tenant_context(
            request,
            tenant["id"],
            admin_message=admin_message,
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
async def ui_saas_admin_billing_checkout(request: Request, tenant_id: str):
    ctx = admin_ui_redirect(request)
    if not isinstance(ctx, AuthContext):
        return ctx
    if not db.get_tenant(tenant_id):
        return RedirectResponse("/ui/saas/app/admin?error=tenant+not+found", status_code=303)
    try:
        session = await run_sync(billing.create_checkout_session, tenant_id)
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
            result = await run_sync(
                sell.publish_and_sell,
                ctx.tenant_id,
                run_id=run_id,
                batch_id=batch_id,
                label=offer_label,
            )
            return _sell_redirect(result)

        if action == "upload_publish":
            if not gallery_name or not files:
                return _sell_error_redirect("gallery name and photos are required")
            batch = await run_sync(
                uploads.create_batch,
                tenant_id=ctx.tenant_id,
                name=gallery_name,
            )
            new_batch_id = batch["id"]
            try:
                await uploads.add_upload_files(
                    tenant_id=ctx.tenant_id,
                    batch_id=new_batch_id,
                    files=files,
                )
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
                analyze_result = await run_sync(
                    service.analyze_upload_batch,
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
            result = await run_sync(
                sell.publish_and_sell,
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
    batch = await run_sync(
        uploads.create_batch,
        tenant_id=ctx.tenant_id,
        name=gallery_name,
    )
    batch_id = batch["id"]
    try:
        await uploads.add_upload_files(
            tenant_id=ctx.tenant_id,
            batch_id=batch_id,
            files=files,
        )
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
            result = await run_sync(
                service.analyze_upload_batch,
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
async def ui_saas_upload_analyze(
    request: Request,
    batch_id: str,
    argus_run_id: int | None = Form(None),
):
    ctx = tenant_ui_redirect(request)
    if not isinstance(ctx, AuthContext):
        return ctx
    try:
        result = await run_sync(
            service.analyze_upload_batch,
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
async def ui_saas_mise_recommend(
    request: Request,
    gallery_id: int,
    argus_run_id: int | None = Form(None),
):
    ctx = tenant_ui_redirect(request)
    if not isinstance(ctx, AuthContext):
        return ctx
    try:
        result = await run_sync(
            service.analyze_mise_gallery,
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



def _apply_catalog_form(tenant_id: str, form) -> str | None:
    for product in catalog.PRODUCTS:
        sku = product.sku
        cents_raw = form.get(f"cents_{sku}")
        label_raw = form.get(f"label_{sku}")
        active = f"active_{sku}" in form
        has_cents = bool(cents_raw and str(cents_raw).strip())
        has_label = bool(label_raw and str(label_raw).strip())
        if not has_cents and not has_label and active:
            db.delete_product_override(tenant_id, sku)
            continue
        if has_cents:
            try:
                unit_cents = int(str(cents_raw).strip())
            except ValueError:
                return f"invalid price for {sku}"
        else:
            unit_cents = product.unit_cents
        label = str(label_raw).strip() if has_label else None
        db.upsert_product_override(
            tenant_id,
            sku,
            unit_cents=unit_cents,
            label=label,
            active=active,
        )
    return None


@router.post("/ui/saas/app/catalog")
async def ui_saas_tenant_catalog_save(request: Request):
    ctx = tenant_ui_redirect(request)
    if not isinstance(ctx, AuthContext):
        return ctx
    form = await request.form()
    err = await run_sync(_apply_catalog_form, ctx.tenant_id, form)
    if err:
        return RedirectResponse(
            f"/ui/saas/app/catalog?error={quote_plus(err)}",
            status_code=303,
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



@router.post("/ui/saas/app/run-edit")
async def ui_saas_run_edit(request: Request, run_id: int = Form(...)):
    ctx = tenant_ui_redirect(request)
    if not isinstance(ctx, AuthContext):
        return ctx
    from .homelab_ui import _run_edit_context

    try:
        await run_sync(
            save_run_edits,
            run_id=run_id,
            tenant_id=ctx.tenant_id,
            bundle_edits=parse_bundle_form(await request.form()),
        )
    except BundleEditError as exc:
        ctx_data = _run_edit_context(request, run_id, edit_error=str(exc))
        if not ctx_data:
            return HTMLResponse("Run not found", status_code=404)
        return templates.TemplateResponse(request, "run_edit.html", ctx_data, status_code=400)
    audit.record("run.bundles.edit", request=request, ctx=ctx, resource=str(run_id))
    return RedirectResponse(f"/runs/{run_id}?edited=1", status_code=303)



@router.post("/ui/saas/app/share-link")
async def ui_saas_create_share_link(
    request: Request,
    run_id: int = Form(...),
    label: str | None = Form(None),
):
    ctx = tenant_ui_redirect(request)
    if not isinstance(ctx, AuthContext):
        return ctx
    try:
        link = await run_sync(
            create_share_link,
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
async def ui_saas_billing_checkout(request: Request):
    ctx = tenant_ui_redirect(request)
    if not isinstance(ctx, AuthContext):
        return ctx
    try:
        session = await run_sync(billing.create_checkout_session, ctx.tenant_id)
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
async def ui_saas_billing_portal(request: Request):
    ctx = tenant_ui_redirect(request)
    if not isinstance(ctx, AuthContext):
        return ctx
    try:
        portal = await run_sync(billing.create_billing_portal_session, ctx.tenant_id)
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


