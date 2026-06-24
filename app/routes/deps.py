"""Shared route dependencies — templates, auth helpers, UI context."""
from __future__ import annotations

from pathlib import Path

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from .. import billing, config, db, homelab, signup, storage, ui_sessions
from ..auth import resolve_auth
from ..auth_context import AuthContext

_ROOT = Path(__file__).resolve().parent.parent.parent
templates = Jinja2Templates(directory=str(_ROOT / "templates"))


def _fmt_cents(cents: int) -> str:
    return f"${cents / 100:,.2f}"


templates.env.filters["money"] = _fmt_cents


def ui_context(request: Request | None = None, **extra) -> dict:
    from .. import argus_client, mise_client

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


def request_auth(request: Request) -> AuthContext | None:
    return getattr(request.state, "auth", None)


def ui_saas_auth(request: Request) -> AuthContext | None:
    ctx = request_auth(request)
    if ctx is not None:
        return ctx
    try:
        return resolve_auth(request)
    except HTTPException:
        return None


def error(message: str, status_code: int) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status_code)


def tenant_ui_redirect(request: Request) -> AuthContext | RedirectResponse:
    ctx = ui_saas_auth(request)
    if ctx is None or ctx.is_admin or not ctx.tenant:
        return RedirectResponse("/ui/saas/login", status_code=303)
    return ctx


def admin_ui_redirect(request: Request) -> AuthContext | RedirectResponse:
    ctx = ui_saas_auth(request)
    if ctx is None or not ctx.is_admin:
        return RedirectResponse("/ui/saas/login", status_code=303)
    return ctx


def admin_tenant_context(
    request: Request,
    tenant_id: str,
    *,
    admin_message: str | None = None,
    admin_error: str | None = None,
    issued_api_key: str | None = None,
) -> dict:
    from ..metering import usage_snapshot

    tenant = db.get_tenant(tenant_id)
    if not tenant:
        raise HTTPException(status_code=404, detail="tenant not found")
    return ui_context(
        request,
        title=f"Tenant {tenant_id}",
        tenant=tenant,
        usage=usage_snapshot(tenant_id),
        keys=db.list_tenant_keys(tenant_id),
        orders=db.list_orders(tenant_id=tenant_id, limit=15),
        admin_message=admin_message,
        admin_error=admin_error,
        issued_api_key=issued_api_key,
    )