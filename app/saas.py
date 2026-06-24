"""SaaS helpers — tenant isolation and request guards."""
from __future__ import annotations

import logging
import sys

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

from . import config, db
from .auth import resolve_auth
from .auth_context import AuthContext

log = logging.getLogger("plutus.saas")

def _public_paths() -> frozenset[str]:
    paths = {
        "/healthz",
        "/saas/status",
        "/saas/billing/status",
        "/webhooks/stripe",
        "/webhooks/whcc",
        "/webhooks/mise/gallery-published",
        "/metrics",
    }
    if not (config.SAAS_MODE and config.SAAS_DISABLE_OPENAPI):
        paths.update({"/openapi.json", "/docs", "/redoc"})
    return frozenset(paths)

SAAS_PUBLIC_UI_PREFIXES = (
    "/ui/saas",
    "/ui/saas/login",
    "/ui/saas/signup",
    "/ui/saas/verify-email",
    "/ui/saas/resend-verification",
    "/static/",
)

SAAS_PUBLIC_STORE_PREFIXES = (
    "/store/",
)

SAAS_PROTECTED_PREFIXES = (
    "/runs",
    "/ui/saas/app",
    "/admin/",
)

SAAS_AUTH_OWNED_PREFIXES = (
    "/analyze",
    "/analyze-folder",
    "/recommend/",
)


def validate_saas_startup() -> None:
    if not config.SAAS_MODE:
        return
    if "pytest" in sys.modules:
        return
    if not config.API_TOKEN:
        raise RuntimeError("PLUTUS_SAAS_MODE requires PLUTUS_API_TOKEN for admin access")
    weak_peppers = {None, "", "plutus-dev-pepper"}
    if config.TENANT_KEY_PEPPER in weak_peppers or config.TENANT_KEY_PEPPER == config.API_TOKEN:
        raise RuntimeError(
            "PLUTUS_TENANT_KEY_PEPPER must be set to a distinct secret in SaaS mode "
            "(not empty, not plutus-dev-pepper, not equal to PLUTUS_API_TOKEN)"
        )
    from . import billing

    if billing.stripe_configured():
        if config.ALLOW_SIMULATE_PAYMENT and not billing.stripe_test_mode():
            log.warning(
                "PLUTUS_ALLOW_SIMULATE_PAYMENT is on with live Stripe keys — disable in prod"
            )
        if not billing.billing_enabled():
            log.warning(
                "STRIPE_PRICE_ID missing — tenant subscriptions disabled; "
                "client bundle checkout still works with STRIPE_SECRET_KEY"
            )
        if not config.STRIPE_WEBHOOK_SECRET:
            log.warning("STRIPE_WEBHOOK_SECRET unset — /webhooks/stripe will reject events")


def tenant_scope(ctx: AuthContext | None) -> str | None:
    if not config.SAAS_MODE or ctx is None:
        return None
    if ctx.is_admin:
        return None
    return ctx.tenant_id


def get_run_for_ctx(run_id: int, ctx: AuthContext | None) -> dict | None:
    return db.get_run(run_id, tenant_id=tenant_scope(ctx))


def _path_requires_saas_auth(path: str) -> bool:
    if path in _public_paths():
        return False
    if any(path.startswith(prefix) for prefix in SAAS_PUBLIC_STORE_PREFIXES):
        return False
    if path.startswith("/ui/saas") and not path.startswith("/ui/saas/app"):
        return False
    if any(path.startswith(prefix) for prefix in SAAS_PUBLIC_UI_PREFIXES):
        return False
    if path.startswith("/static"):
        return False
    if any(path.startswith(prefix) for prefix in SAAS_AUTH_OWNED_PREFIXES):
        return False
    if path == "/":
        return config.SAAS_MODE
    return any(path.startswith(prefix) for prefix in SAAS_PROTECTED_PREFIXES)


async def saas_auth_middleware(request: Request, call_next):
    if not config.SAAS_MODE:
        return await call_next(request)

    path = request.url.path
    if not _path_requires_saas_auth(path):
        return await call_next(request)

    if getattr(request.state, "auth", None) is not None:
        return await call_next(request)

    try:
        ctx = resolve_auth(request, authorization=request.headers.get("Authorization"))
    except HTTPException as exc:
        if path.startswith("/ui/") or (path.startswith("/runs/") and request.method == "GET"):
            from fastapi.responses import PlainTextResponse

            return PlainTextResponse("Authentication required", status_code=exc.status_code)
        return JSONResponse({"error": exc.detail}, status_code=exc.status_code)

    request.state.auth = ctx
    return await call_next(request)