"""Signup email verification — token issue, verify, resend."""
from __future__ import annotations

import logging
import secrets
from datetime import UTC, datetime, timedelta

from . import config, db, notifications

log = logging.getLogger("plutus.signup_verify")


class SignupVerifyError(Exception):
    """Verification flow failure."""


def verification_enabled() -> bool:
    if not config.SAAS_MODE or not config.SIGNUP_ENABLED:
        return False
    if not config.SIGNUP_VERIFY_EMAIL:
        return False
    if config.SIGNUP_VERIFY_DEV_BYPASS:
        return False
    return notifications._smtp_ready()


def tenant_email_verified(tenant: dict | None) -> bool:
    if not tenant:
        return False
    if not verification_enabled():
        return True
    return bool(tenant.get("email_verified_at"))


def _verify_url(token: str) -> str:
    base = config.SAAS_PUBLIC_URL.rstrip("/")
    return f"{base}/ui/saas/verify-email?token={token}"


def create_pending_verification(
    *,
    tenant_id: str,
    email: str,
    key_id: str | None = None,
) -> str | None:
    """Store verification token; return token when email verification is active."""
    if not verification_enabled():
        db.update_tenant(tenant_id, email_verified_at=datetime.now(UTC).isoformat())
        return None
    token = secrets.token_urlsafe(32)
    expires = datetime.now(UTC) + timedelta(hours=config.SIGNUP_VERIFY_TOKEN_HOURS)
    db.insert_signup_verification(
        token=token,
        tenant_id=tenant_id,
        email=email.strip().lower(),
        key_id=key_id,
        expires_at=expires.isoformat(),
    )
    return token


def send_verification_email(*, tenant_id: str, email: str, token: str) -> bool:
    link = _verify_url(token)
    body = (
        f"Confirm your email to activate your Plutus trial.\n\n"
        f"Verify: {link}\n\n"
        f"This link expires in {config.SIGNUP_VERIFY_TOKEN_HOURS} hours."
    )
    sent = notifications._send_email(
        to=email,
        subject="Confirm your Plutus studio email",
        body=body,
    )
    if not sent:
        log.warning("verification email not sent for tenant %s (SMTP unavailable?)", tenant_id)
    return sent


def verify_token(token: str) -> dict:
    row = db.get_signup_verification(token.strip())
    if not row:
        raise SignupVerifyError("invalid or expired verification link")
    if row.get("verified_at"):
        raise SignupVerifyError("this email was already verified — sign in to continue")
    expires = row.get("expires_at")
    if expires:
        try:
            exp_dt = datetime.fromisoformat(str(expires).replace("Z", "+00:00"))
            if exp_dt.tzinfo is None:
                exp_dt = exp_dt.replace(tzinfo=UTC)
            if datetime.now(UTC) > exp_dt:
                raise SignupVerifyError(
                    "verification link expired — sign up again or contact support"
                )
        except ValueError as exc:
            raise SignupVerifyError("invalid verification link") from exc
    tenant_id = row["tenant_id"]
    tenant = db.get_tenant(tenant_id)
    if not tenant:
        raise SignupVerifyError("studio account not found")
    now = datetime.now(UTC).isoformat()
    db.mark_signup_verification_verified(token.strip(), verified_at=now)
    db.update_tenant(tenant_id, email_verified_at=now, notify_email=row["email"])
    tenant = db.get_tenant(tenant_id) or tenant
    from . import tenants

    legacy_key_id = row.get("key_id")
    if legacy_key_id:
        tenants.revoke_key(legacy_key_id)
    issued = tenants.issue_api_key(tenant_id, label="signup")
    api_key = issued["api_key"]
    return {
        "tenant": tenant,
        "api_key": api_key,
        "key_id": issued["key_id"],
        "store_url": f"/store/{tenant.get('store_slug') or tenant_id}",
    }


def resend_for_email(email: str) -> bool:
    """Re-send verification for the newest pending tenant matching email."""
    if not verification_enabled():
        return False
    row = db.get_pending_signup_verification_by_email(email.strip().lower())
    if not row:
        return False
    return send_verification_email(
        tenant_id=row["tenant_id"],
        email=row["email"],
        token=row["token"],
    )


def resend_attempted(email: str) -> bool:
    """Whether a resend could have been sent (for anti-enumeration UI)."""
    if not verification_enabled():
        return False
    return bool(db.get_pending_signup_verification_by_email(email.strip().lower()))