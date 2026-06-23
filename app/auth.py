"""Bearer auth for Mise callbacks, homelab admin, and SaaS tenant API keys."""
from __future__ import annotations

import secrets

from fastapi import Header, HTTPException, Request

from . import config, tenants
from .auth_context import AuthContext, set_auth_context

UI_TOKEN_COOKIE = "plutus_ui_token"


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
    cookie = request.cookies.get(UI_TOKEN_COOKIE)
    if cookie and cookie.strip():
        return cookie.strip()
    return None


def resolve_auth(
    request: Request,
    *,
    authorization: str | None = None,
    form_token: str | None = None,
) -> AuthContext:
    provided = token_from_request(
        request, authorization=authorization, form_token=form_token
    )

    if config.SAAS_MODE:
        if config.API_TOKEN and provided == config.API_TOKEN:
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
    if provided != config.API_TOKEN:
        raise HTTPException(status_code=401, detail="invalid bearer token")

    ctx = AuthContext(is_admin=True)
    set_auth_context(ctx)
    request.state.auth = ctx
    return ctx


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