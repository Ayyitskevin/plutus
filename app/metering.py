"""Tenant usage metering, trial enforcement, and cap warnings."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from . import config, db


class MeteringError(Exception):
    """Usage cap exceeded or subscription inactive."""


def trial_days_remaining(tenant: dict) -> int | None:
    """Days left in signup trial, or None if not on trial."""
    if config.SIGNUP_TRIAL_DAYS <= 0:
        return None
    if tenant.get("billing_status") == "active":
        return None
    created = tenant.get("created_at")
    if not created:
        return None
    try:
        start = datetime.fromisoformat(str(created).replace("Z", "+00:00"))
        if start.tzinfo is None:
            start = start.replace(tzinfo=UTC)
        end = start + timedelta(days=config.SIGNUP_TRIAL_DAYS)
        remaining = (end - datetime.now(UTC)).days
        return max(0, remaining)
    except ValueError:
        return None


def _trial_expired(tenant: dict) -> bool:
    if config.SIGNUP_TRIAL_DAYS <= 0:
        return False
    if tenant.get("billing_status") == "active":
        return False
    created = tenant.get("created_at")
    if not created:
        return False
    try:
        start = datetime.fromisoformat(str(created).replace("Z", "+00:00"))
        if start.tzinfo is None:
            start = start.replace(tzinfo=UTC)
        end = start + timedelta(days=config.SIGNUP_TRIAL_DAYS)
        return datetime.now(UTC) > end
    except ValueError:
        return False


def usage_snapshot(tenant_id: str) -> dict:
    usage = db.get_tenant_usage(tenant_id)
    tenant = db.get_tenant(tenant_id) or {}
    cap = tenant.get("monthly_recommend_cap")
    warnings = []
    if cap and cap > 0:
        pct = round(100 * usage["recommends"] / cap, 1)
        if pct >= 80:
            warnings.append(
                {
                    "kind": "recommend_cap",
                    "pct": pct,
                    "message": f"{usage['recommends']} of {cap} recommendations used this month",
                }
            )
    if _trial_expired(tenant):
        warnings.append(
            {
                "kind": "trial_expired",
                "pct": 100,
                "message": (
                    f"Trial ended — subscribe to continue "
                    f"({config.SIGNUP_TRIAL_DAYS} day limit)"
                ),
            }
        )
    elif tenant.get("billing_status") == "trialing" and config.SIGNUP_TRIAL_DAYS > 0:
        days_left = trial_days_remaining(tenant)
        msg = (
            f"Trial — {days_left} day(s) remaining"
            if days_left is not None
            else f"Trial active — {config.SIGNUP_TRIAL_DAYS} days from signup"
        )
        warnings.append({"kind": "trial_active", "pct": 0, "message": msg})
    return {
        "tenant": usage,
        "caps": {"monthly_recommends": cap},
        "warnings": warnings,
        "trial_expired": _trial_expired(tenant),
        "trial_days_remaining": trial_days_remaining(tenant),
        "needs_subscription": tenant.get("billing_status") not in {"active"},
    }


def check_recommend_cap(tenant_id: str) -> None:
    tenant = db.get_tenant(tenant_id)
    if not tenant:
        raise MeteringError(f"tenant not found: {tenant_id}")

    if not tenant.get("active", True):
        raise MeteringError("tenant account is inactive")

    billing = tenant.get("billing_status") or ""
    if billing in {"canceled", "unpaid", "past_due"}:
        raise MeteringError("subscription inactive — update billing to continue")

    if _trial_expired(tenant) and billing != "active":
        raise MeteringError(
            f"trial expired after {config.SIGNUP_TRIAL_DAYS} days — subscribe to continue"
        )

    cap = tenant.get("monthly_recommend_cap")
    if not cap or cap <= 0:
        return
    usage = db.get_tenant_usage(tenant_id)
    if usage["recommends"] >= cap:
        raise MeteringError(
            f"monthly recommendation cap reached ({cap}); upgrade plan or wait for next period"
        )


def record_recommend(tenant_id: str) -> dict:
    return db.increment_tenant_usage(tenant_id, recommends=1)