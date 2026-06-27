"""Bearer auth for the Mise recommend/callback path (studio mode — token only)."""
from __future__ import annotations

import secrets

from fastapi import Header, HTTPException, Request

from . import config
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


def resolve_auth(
    request: Request,
    *,
    authorization: str | None = None,
    form_token: str | None = None,
) -> AuthContext:
    """Token-only auth. Open when no API token is configured (studio dev default)."""
    provided = token_from_request(
        request, authorization=authorization, form_token=form_token
    )

    if not config.API_TOKEN:
        ctx = AuthContext(is_admin=True)
    elif not provided:
        raise HTTPException(status_code=401, detail="missing bearer token")
    elif not secrets.compare_digest(provided, config.API_TOKEN):
        raise HTTPException(status_code=401, detail="invalid bearer token")
    else:
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
