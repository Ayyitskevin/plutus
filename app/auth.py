"""Bearer auth for Mise callbacks, homelab admin, and SaaS tenant API keys."""
from __future__ import annotations

import secrets

from fastapi import Form, Header, HTTPException, Request

from . import config, db, tenants, ui_sessions
from .auth_context import AuthContext, set_auth_context


def token_from_request(
    request: Request,
    *,
    authorization: str | None = None,
    form_token: str | None = None,
) -> str | None:
    if form_token and form_token.strip():
        return form_token.strip()
    if authorization and authorization.startswith("Bearer "):
        return authorization.removeprefix("Bearer ").strip()
    return None


def _ctx_from_session(
    request: Request,
    session: dict,
    *,
    session_id: str | None = None,
) -> AuthContext:
    if session.get("is_admin"):
        ctx = AuthContext(is_admin=True)
    else:
        tenant_id = session.get("tenant_id")
        tenant = db.get_tenant(tenant_id) if tenant_id else None
        if not tenant or not tenant.get("active", True):
            if session_id:
                ui_sessions.delete_session(session_id)
            raise HTTPException(status_code=401, detail="session invalid")
        api_key_id = session.get("api_key_id")
        if api_key_id:
            key = db.get_tenant_api_key(str(api_key_id))
            if not key or key.get("revoked_at"):
                if session_id:
                    ui_sessions.delete_session(session_id)
                raise HTTPException(status_code=401, detail="session invalid")
        ctx = AuthContext(tenant=tenant, api_key_id=api_key_id)
    set_auth_context(ctx)
    request.state.auth = ctx
    request.state.ui_session = session
    return ctx


def resolve_auth(
    request: Request,
    *,
    authorization: str | None = None,
    form_token: str | None = None,
) -> AuthContext:
    session_id = request.cookies.get(ui_sessions.UI_SESSION_COOKIE)
    session = ui_sessions.get_session(session_id)
    if session:
        return _ctx_from_session(request, session, session_id=session_id)

    provided = token_from_request(
        request, authorization=authorization, form_token=form_token
    )

    if config.SAAS_MODE:
        if config.API_TOKEN and provided and secrets.compare_digest(provided, config.API_TOKEN):
            ctx = AuthContext(is_admin=True)
            set_auth_context(ctx)
            request.state.auth = ctx
            return ctx
        resolved = tenants.resolve_api_key(provided)
        if resolved:
            tenant, key_id = resolved
            ctx = AuthContext(tenant=tenant, api_key_id=key_id)
            set_auth_context(ctx)
            request.state.auth = ctx
            return ctx
        raise HTTPException(status_code=401, detail="missing or invalid tenant API key")

    if not config.API_TOKEN:
        ctx = AuthContext(is_admin=True)
        set_auth_context(ctx)
        request.state.auth = ctx
        return ctx

    if not provided:
        raise HTTPException(status_code=401, detail="missing bearer token")
    if not secrets.compare_digest(provided, config.API_TOKEN):
        raise HTTPException(status_code=401, detail="invalid bearer token")

    ctx = AuthContext(is_admin=True)
    set_auth_context(ctx)
    request.state.auth = ctx
    return ctx


def verify_ui_csrf(request: Request, csrf_token: str = Form("")) -> None:
    """Require CSRF token for cookie-session POSTs."""
    session = getattr(request.state, "ui_session", None)
    if session is None:
        session_id = request.cookies.get(ui_sessions.UI_SESSION_COOKIE)
        session = ui_sessions.get_session(session_id)
    if not session:
        return
    if not ui_sessions.validate_csrf(session, csrf_token or request.headers.get("X-CSRF-Token")):
        raise HTTPException(status_code=403, detail="invalid or missing CSRF token")


def verify_api_access(
    request: Request,
    *,
    authorization: str | None = None,
    form_token: str | None = None,
) -> AuthContext:
    return resolve_auth(request, authorization=authorization, form_token=form_token)


def require_bearer(
    request: Request,
    authorization: str | None = Header(default=None),
) -> AuthContext:
    return resolve_auth(request, authorization=authorization)


def require_admin(
    request: Request,
    authorization: str | None = Header(default=None),
) -> AuthContext:
    ctx = resolve_auth(request, authorization=authorization)
    if not ctx.is_admin:
        raise HTTPException(status_code=403, detail="admin token required")
    return ctx


def require_token(request: Request) -> None:
    """Legacy Mise inbound guard — homelab API_TOKEN only."""
    if not config.API_TOKEN:
        return
    header = request.headers.get("Authorization", "")
    expected = f"Bearer {config.API_TOKEN}"
    if not secrets.compare_digest(header, expected):
        raise HTTPException(status_code=401, detail="bad token")