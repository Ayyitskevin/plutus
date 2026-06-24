"""Admin invite tokens — one-time claim URL instead of API key in email."""
from __future__ import annotations

import logging
import secrets
from datetime import UTC, datetime, timedelta

from . import config, db, notifications

log = logging.getLogger("plutus.tenant_invite")


class TenantInviteError(Exception):
    """Invite claim failure."""


def invite_flow_enabled() -> bool:
    return bool(config.SAAS_MODE and notifications.smtp_ready())


def claim_url(token: str) -> str:
    base = config.SAAS_PUBLIC_URL.rstrip("/")
    return f"{base}/ui/saas/claim-invite?token={token}"


def create_invite(*, tenant_id: str, email: str) -> str:
    """Invalidate pending invites and mint a fresh claim token."""
    db.revoke_pending_tenant_invites(tenant_id)
    token = secrets.token_urlsafe(32)
    expires = datetime.now(UTC) + timedelta(hours=config.TENANT_INVITE_TOKEN_HOURS)
    db.insert_tenant_invite(
        token=token,
        tenant_id=tenant_id,
        email=email.strip().lower(),
        expires_at=expires.isoformat(),
    )
    return token


def claim_token(token: str) -> dict:
    row = db.get_tenant_invite(token.strip())
    if not row:
        raise TenantInviteError("invalid or expired invite link")
    if row.get("claimed_at"):
        raise TenantInviteError("this invite was already claimed — sign in to continue")
    expires = row.get("expires_at")
    if expires:
        try:
            exp_dt = datetime.fromisoformat(str(expires).replace("Z", "+00:00"))
            if exp_dt.tzinfo is None:
                exp_dt = exp_dt.replace(tzinfo=UTC)
            if datetime.now(UTC) > exp_dt:
                raise TenantInviteError(
                    "invite link expired — ask your admin to resend the welcome email"
                )
        except ValueError as exc:
            raise TenantInviteError("invalid invite link") from exc
    tenant_id = row["tenant_id"]
    tenant = db.get_tenant(tenant_id)
    if not tenant:
        raise TenantInviteError("studio account not found")
    now = datetime.now(UTC).isoformat()
    from . import tenants

    issued = tenants.issue_api_key(tenant_id, label="invite")
    db.mark_tenant_invite_claimed(token.strip(), claimed_at=now)
    slug = tenant.get("store_slug") or tenant_id
    return {
        "tenant": tenant,
        "api_key": issued["api_key"],
        "key_id": issued["key_id"],
        "store_url": f"/store/{slug}",
    }