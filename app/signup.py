"""Public tenant self-registration for SaaS mode."""
from __future__ import annotations

import re

from . import config, db, signup_verify, tenants
from .tenants import TenantError

_SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")


class SignupError(Exception):
    """Registration validation failure."""


def signup_enabled() -> bool:
    return config.SAAS_MODE and config.SIGNUP_ENABLED


def _slugify(name: str) -> str:
    slug = name.strip().lower()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    slug = slug.strip("-")
    return slug[:48] or "studio"


def _validate_slug(slug: str) -> None:
    if not slug or not _SLUG_RE.match(slug):
        raise SignupError(
            "store slug must be 2–64 chars, lowercase letters, numbers, and hyphens"
        )


def register_studio(
    *,
    studio_name: str,
    email: str,
    store_slug: str | None = None,
) -> dict:
    """Create trial tenant, issue API key, return onboarding payload."""
    if not signup_enabled():
        raise SignupError("self-service signup is disabled")

    name = (studio_name or "").strip()
    if len(name) < 2:
        raise SignupError("studio name is required")
    addr = (email or "").strip().lower()
    if "@" not in addr or "." not in addr.split("@")[-1]:
        raise SignupError("valid email is required")

    slug = _slugify(store_slug or name)
    _validate_slug(slug)
    tenant_id = slug

    try:
        tenant = tenants.create_tenant(
            tenant_id,
            name=name,
            store_slug=slug,
            monthly_recommend_cap=config.SIGNUP_TRIAL_RECOMMEND_CAP,
        )
    except TenantError as exc:
        raise SignupError(str(exc)) from exc

    db.update_tenant(
        tenant_id,
        notify_email=addr,
        plan_tier="trial",
        billing_status="trialing",
    )
    tenant = db.get_tenant(tenant_id) or tenant
    verify_token = signup_verify.create_pending_verification(
        tenant_id=tenant_id,
        email=addr,
        key_id=None,
    )
    if verify_token:
        signup_verify.send_verification_email(
            tenant_id=tenant_id,
            email=addr,
            token=verify_token,
        )
        return {
            "tenant": tenant,
            "api_key": None,
            "store_url": f"/store/{slug}",
            "verification_required": True,
            "verify_email": addr,
        }
    issued = tenants.issue_api_key(tenant_id, label="signup")
    return {
        "tenant": tenant,
        "api_key": issued["api_key"],
        "key_id": issued["key_id"],
        "store_url": f"/store/{slug}",
        "verification_required": False,
        "verify_email": None,
    }