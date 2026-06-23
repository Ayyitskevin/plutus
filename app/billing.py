"""Stripe billing — tenant subscriptions and webhook handling."""
from __future__ import annotations

import hashlib
import hmac
import logging
import time
from typing import Any

import httpx

from . import config, db

log = logging.getLogger("plutus.billing")

STRIPE_API = "https://api.stripe.com/v1"


class BillingError(Exception):
    """Raised when billing configuration or Stripe calls fail."""


def _stripe_value_ok(value: str | None) -> bool:
    if not value or not str(value).strip():
        return False
    upper = str(value).upper()
    return "CHANGE_ME" not in upper


def billing_enabled() -> bool:
    return _stripe_value_ok(config.STRIPE_SECRET_KEY) and _stripe_value_ok(config.STRIPE_PRICE_ID)


def stripe_configured() -> bool:
    return _stripe_value_ok(config.STRIPE_SECRET_KEY)


def stripe_test_mode() -> bool:
    key = config.STRIPE_SECRET_KEY or ""
    return key.startswith("sk_test_") or key.startswith("rk_test_")


def billing_status() -> dict:
    return {
        "enabled": billing_enabled(),
        "test_mode": stripe_test_mode(),
        "price_id": config.STRIPE_PRICE_ID,
        "webhook_configured": bool(config.STRIPE_WEBHOOK_SECRET),
        "success_url": config.STRIPE_SUCCESS_URL,
        "cancel_url": config.STRIPE_CANCEL_URL,
    }


def stripe_connectivity() -> dict[str, Any]:
    """Ping Stripe API when configured (for health / dogfood)."""
    if not stripe_configured():
        return {"configured": False, "reachable": False}
    try:
        _stripe_request("GET", "/balance")
        return {
            "configured": True,
            "reachable": True,
            "test_mode": stripe_test_mode(),
        }
    except BillingError as exc:
        return {"configured": True, "reachable": False, "detail": str(exc)[:200]}


def sign_webhook_payload(payload: bytes, *, secret: str | None = None) -> str:
    """Build a Stripe-Signature header for local webhook testing."""
    wh_secret = secret or config.STRIPE_WEBHOOK_SECRET or ""
    ts = str(int(time.time()))
    signed = f"{ts}.{payload.decode('utf-8')}".encode()
    digest = hmac.new(wh_secret.encode("utf-8"), signed, hashlib.sha256).hexdigest()
    return f"t={ts},v1={digest}"


def _stripe_request(method: str, path: str, data: dict | None = None) -> dict:
    if not config.STRIPE_SECRET_KEY:
        raise BillingError("STRIPE_SECRET_KEY is not set")
    url = f"{STRIPE_API}{path}"
    with httpx.Client(timeout=30.0) as client:
        resp = client.request(
            method,
            url,
            data=data,
            auth=(config.STRIPE_SECRET_KEY, ""),
        )
    if resp.status_code >= 400:
        raise BillingError(f"Stripe HTTP {resp.status_code}: {resp.text[:400]}")
    return resp.json()


def ensure_stripe_customer(tenant_id: str) -> str:
    tenant = db.get_tenant(tenant_id)
    if not tenant:
        raise BillingError(f"tenant not found: {tenant_id}")
    existing = tenant.get("stripe_customer_id")
    if existing:
        return existing
    body = {
        "name": tenant["name"],
        "metadata[tenant_id]": tenant_id,
    }
    customer = _stripe_request("POST", "/customers", body)
    customer_id = customer["id"]
    db.update_tenant(
        tenant_id,
        stripe_customer_id=customer_id,
        billing_status=tenant.get("billing_status") or "pending",
    )
    return customer_id


def create_checkout_session(tenant_id: str) -> dict:
    if not billing_enabled():
        raise BillingError("Stripe billing is not configured (STRIPE_SECRET_KEY + STRIPE_PRICE_ID)")
    customer_id = ensure_stripe_customer(tenant_id)
    session = _stripe_request(
        "POST",
        "/checkout/sessions",
        {
            "mode": "subscription",
            "customer": customer_id,
            "line_items[0][price]": config.STRIPE_PRICE_ID,
            "line_items[0][quantity]": "1",
            "success_url": config.STRIPE_SUCCESS_URL,
            "cancel_url": config.STRIPE_CANCEL_URL,
            "metadata[tenant_id]": tenant_id,
            "metadata[checkout_kind]": "tenant_subscription",
            "subscription_data[metadata][tenant_id]": tenant_id,
        },
    )
    return {"checkout_url": session["url"], "session_id": session["id"]}


def create_billing_portal_session(tenant_id: str) -> dict:
    if not billing_enabled():
        raise BillingError("Stripe billing is not configured")
    tenant = db.get_tenant(tenant_id)
    if not tenant:
        raise BillingError(f"tenant not found: {tenant_id}")
    customer_id = tenant.get("stripe_customer_id") or ensure_stripe_customer(tenant_id)
    portal = _stripe_request(
        "POST",
        "/billing_portal/sessions",
        {
            "customer": customer_id,
            "return_url": config.STRIPE_BILLING_PORTAL_RETURN_URL,
        },
    )
    return {"portal_url": portal["url"]}


def verify_webhook_signature(payload: bytes, sig_header: str | None) -> bool:
    if not config.STRIPE_WEBHOOK_SECRET or not sig_header:
        return False
    parts = {}
    for item in sig_header.split(","):
        key, _, value = item.partition("=")
        parts[key] = value
    timestamp = parts.get("t")
    signature = parts.get("v1")
    if not timestamp or not signature:
        return False
    try:
        if abs(time.time() - int(timestamp)) > 300:
            return False
    except ValueError:
        return False
    signed = f"{timestamp}.{payload.decode('utf-8')}".encode()
    expected = hmac.new(
        config.STRIPE_WEBHOOK_SECRET.encode("utf-8"),
        signed,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


def handle_webhook_event(event: dict[str, Any]) -> None:
    from . import orders as orders_mod

    etype = event.get("type") or "unknown"
    event_id = event.get("id")
    if event_id and not db.record_stripe_webhook_event(event_id, etype):
        log.info("skipping duplicate stripe event %s (%s)", event_id, etype)
        return

    obj = (event.get("data") or {}).get("object") or {}
    metadata = obj.get("metadata") or {}
    tenant_id = metadata.get("tenant_id")
    checkout_kind = metadata.get("checkout_kind")

    def _resolve_tenant_id() -> str | None:
        if tenant_id:
            return tenant_id
        meta_tid = (obj.get("metadata") or {}).get("tenant_id")
        if meta_tid:
            return meta_tid
        customer_id = obj.get("customer")
        if customer_id:
            row = db.get_tenant_by_stripe_customer(str(customer_id))
            if row:
                return row["id"]
        return None

    if etype == "checkout.session.completed":
        if checkout_kind == "client_bundle" or metadata.get("order_id"):
            orders_mod.handle_checkout_completed(obj)
            return
        sub_id = obj.get("subscription")
        customer_id = obj.get("customer")
        tid = _resolve_tenant_id()
        if tid:
            db.update_tenant(
                tid,
                stripe_customer_id=customer_id,
                stripe_subscription_id=sub_id,
                billing_status="active",
                plan_tier="pro",
                monthly_recommend_cap=500,
            )
            log.info("activated billing for tenant %s", tid)
        return

    if etype in {"customer.subscription.updated", "customer.subscription.created"}:
        tid = _resolve_tenant_id()
        status = obj.get("status")
        if tid and status:
            db.update_tenant(
                tid,
                stripe_subscription_id=obj.get("id"),
                billing_status=status,
                active=status in {"active", "trialing"},
            )
        return

    if etype == "customer.subscription.deleted":
        tid = _resolve_tenant_id()
        if tid:
            db.update_tenant(
                tid,
                billing_status="canceled",
                plan_tier="free",
                stripe_subscription_id=None,
            )
        return

    if etype == "invoice.payment_failed":
        tid = _resolve_tenant_id()
        if tid:
            db.update_tenant(tid, billing_status="past_due", active=False)
            log.warning("tenant %s marked past_due after invoice.payment_failed", tid)
        return