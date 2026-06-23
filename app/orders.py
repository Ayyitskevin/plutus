"""Client bundle orders via Stripe one-time checkout."""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from . import billing, config, db, metrics

log = logging.getLogger("plutus.orders")


class OrderError(Exception):
    """Order creation or checkout failure."""


def _bundle_items(bundle: dict[str, Any], tenant_id: str) -> list[dict[str, Any]]:
    overrides = {row["sku"]: row for row in db.list_product_overrides(tenant_id)}
    items = []
    for line in bundle.get("items") or []:
        sku = line.get("sku") or "custom"
        override = overrides.get(sku)
        unit_cents = int(
            override["unit_cents"]
            if override and override.get("unit_cents") is not None
            else line.get("unit_cents") or 0
        )
        label = (
            override.get("label")
            if override and override.get("label")
            else line.get("label") or sku
        )
        items.append(
            {
                "sku": sku,
                "label": label,
                "quantity": int(line.get("quantity") or 1),
                "unit_cents": unit_cents,
            }
        )
    return items


def bundle_total_cents(bundle: dict[str, Any], tenant_id: str) -> int:
    return sum(i["unit_cents"] * i["quantity"] for i in _bundle_items(bundle, tenant_id))


def prepare_bundle_order(
    *,
    tenant_id: str,
    run_id: int,
    bundle_index: int,
    client_email: str | None = None,
    client_name: str | None = None,
) -> dict[str, Any]:
    run = db.get_run(run_id, tenant_id=tenant_id)
    if not run:
        raise OrderError("recommendation run not found")
    bundles = run["payload"].get("bundles") or []
    if bundle_index < 0 or bundle_index >= len(bundles):
        raise OrderError("invalid bundle index")

    bundle = bundles[bundle_index]
    items = _bundle_items(bundle, tenant_id)
    total_cents = sum(i["unit_cents"] * i["quantity"] for i in items)
    if total_cents <= 0:
        raise OrderError("bundle has no priced items")

    order_id = db.create_order(
        tenant_id=tenant_id,
        run_id=run_id,
        bundle_index=bundle_index,
        total_cents=total_cents,
        items=items,
        client_email=client_email,
        client_name=client_name,
    )
    metrics.inc("orders_created")
    metrics.inc_tenant(tenant_id, "orders_created")
    return {
        "order_id": order_id,
        "total_cents": total_cents,
        "items": items,
        "bundle_index": bundle_index,
    }


def create_bundle_checkout(
    *,
    tenant_id: str,
    run_id: int,
    bundle_index: int,
    client_email: str | None = None,
    client_name: str | None = None,
) -> dict:
    if not billing.stripe_configured():
        raise OrderError("Stripe is not configured for client checkout")

    prepared = prepare_bundle_order(
        tenant_id=tenant_id,
        run_id=run_id,
        bundle_index=bundle_index,
        client_email=client_email,
        client_name=client_name,
    )
    order_id = prepared["order_id"]
    items = prepared["items"]

    line_data: dict[str, str] = {}
    for idx, item in enumerate(items):
        line_data[f"line_items[{idx}][price_data][currency]"] = "usd"
        line_data[f"line_items[{idx}][price_data][unit_amount]"] = str(item["unit_cents"])
        line_data[f"line_items[{idx}][price_data][product_data][name]"] = item["label"]
        line_data[f"line_items[{idx}][quantity]"] = str(item["quantity"])

    success_url = f"{config.STRIPE_STORE_SUCCESS_URL}?session_id={{CHECKOUT_SESSION_ID}}"
    cancel_url = f"{config.STRIPE_STORE_CANCEL_URL}?order_id={order_id}"

    session = billing._stripe_request(
        "POST",
        "/checkout/sessions",
        {
            "mode": "payment",
            "customer_email": client_email or "",
            "success_url": success_url,
            "cancel_url": cancel_url,
            "metadata[tenant_id]": tenant_id,
            "metadata[order_id]": str(order_id),
            "metadata[run_id]": str(run_id),
            "metadata[bundle_index]": str(bundle_index),
            "metadata[checkout_kind]": "client_bundle",
            **line_data,
        },
    )
    db.update_order(order_id, stripe_session_id=session["id"])
    return {
        "order_id": order_id,
        "checkout_url": session["url"],
        "session_id": session["id"],
        "total_cents": prepared["total_cents"],
    }


def mark_order_paid(
    order_id: int,
    *,
    client_email: str | None = None,
    client_name: str | None = None,
    stripe_payment_intent: str | None = None,
) -> dict[str, Any]:
    """Mark order paid, submit to lab, and notify studio."""
    order = db.get_order(order_id)
    if not order:
        raise OrderError(f"order not found: {order_id}")
    if order["status"] == "paid":
        return {"order_id": order_id, "status": "paid", "already_paid": True}

    from . import order_tracking

    track_token = order.get("client_token") or order_tracking.ensure_client_token(order_id)
    db.update_order(
        order_id,
        status="paid",
        stripe_payment_intent=stripe_payment_intent,
        paid_at=datetime.now(UTC).isoformat(),
        client_email=client_email or order.get("client_email"),
        client_name=client_name or order.get("client_name"),
        client_token=track_token,
    )
    tenant_id = order["tenant_id"]
    db.increment_tenant_usage(
        tenant_id,
        orders=1,
        revenue_cents=int(order["total_cents"]),
    )
    metrics.inc("orders_paid")
    metrics.inc_tenant(tenant_id, "orders_paid")
    log.info("order %s paid for tenant %s", order_id, tenant_id)

    from . import lab, notifications

    lab_result: dict[str, Any] = {}
    try:
        lab_result = lab.submit_order(order_id)
    except lab.LabError:
        log.exception("lab submission failed for order %s", order_id)

    notify_result = notifications.notify_order_paid(order_id)
    updated = db.get_order(order_id) or order
    return {
        "order_id": order_id,
        "status": "paid",
        "lab_status": updated.get("lab_status"),
        "lab_ref": updated.get("lab_ref"),
        "lab": lab_result,
        "notifications": notify_result,
        "client_track_url": order_tracking.client_track_url(track_token),
    }


def handle_checkout_completed(session: dict[str, Any]) -> None:
    metadata = session.get("metadata") or {}
    order_id = metadata.get("order_id")
    if not order_id:
        order = db.get_order_by_session(session.get("id") or "")
        if not order:
            log.warning("checkout completed without order_id metadata")
            return
        order_id = str(order["id"])
    try:
        mark_order_paid(
            int(order_id),
            client_email=session.get("customer_details", {}).get("email"),
            stripe_payment_intent=session.get("payment_intent"),
        )
    except OrderError as exc:
        log.warning("checkout completion failed: %s", exc)


def simulate_test_payment(
    order_id: int,
    *,
    client_email: str | None = None,
    client_name: str | None = None,
) -> dict[str, Any]:
    """Complete a pending order without Stripe — test mode + explicit opt-in only."""
    if not config.ALLOW_SIMULATE_PAYMENT:
        raise OrderError("payment simulation is disabled (PLUTUS_ALLOW_SIMULATE_PAYMENT)")
    if billing.stripe_configured() and not billing.stripe_test_mode():
        raise OrderError("payment simulation requires Stripe test keys (sk_test_)")
    order = db.get_order(order_id)
    if not order:
        raise OrderError(f"order not found: {order_id}")
    if order["status"] == "paid":
        return mark_order_paid(order_id)
    return mark_order_paid(
        order_id,
        client_email=client_email or order.get("client_email") or "client@dogfood.test",
        client_name=client_name or order.get("client_name") or "Dogfood Client",
        stripe_payment_intent=f"pi_simulated_{order_id}",
    )