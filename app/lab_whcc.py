"""WHCC lab adapter stub — real API wiring ready, simulates when unconfigured."""
from __future__ import annotations

import logging
import uuid
from typing import Any

import httpx

from . import config, db

log = logging.getLogger("plutus.lab.whcc")

_WHCC_FLOW = {
    "submitted": "processing",
    "processing": "shipped",
    "shipped": "complete",
}


def whcc_configured() -> bool:
    return bool(config.WHCC_API_URL and config.WHCC_API_KEY)


def _order_payload(order: dict) -> dict[str, Any]:
    items = []
    for line in order.get("items") or []:
        items.append(
            {
                "sku": line["sku"],
                "description": line["label"],
                "quantity": line["quantity"],
                "unit_price_cents": line["unit_cents"],
            }
        )
    return {
        "external_order_id": str(order["id"]),
        "tenant_id": order["tenant_id"],
        "run_id": order["run_id"],
        "bundle_index": order["bundle_index"],
        "customer_email": order.get("client_email"),
        "customer_name": order.get("client_name"),
        "total_cents": order["total_cents"],
        "line_items": items,
        "account_id": config.WHCC_ACCOUNT_ID,
    }


def submit_order(order_id: int) -> dict[str, Any]:
    order = db.get_order(order_id)
    if not order:
        raise ValueError(f"order not found: {order_id}")

    payload = _order_payload(order)
    if whcc_configured():
        url = f"{config.WHCC_API_URL.rstrip('/')}/orders"
        headers = {
            "Authorization": f"Bearer {config.WHCC_API_KEY}",
            "Content-Type": "application/json",
        }
        with httpx.Client(timeout=30.0) as client:
            resp = client.post(url, json=payload, headers=headers)
        if resp.status_code >= 400:
            log.error("WHCC submit failed %s: %s", resp.status_code, resp.text[:300])
            raise RuntimeError(f"WHCC HTTP {resp.status_code}")
        body = resp.json()
        ref = body.get("order_id") or body.get("id") or f"whcc-{order_id}"
        status = body.get("status") or "submitted"
    else:
        ref = f"whcc-stub-{order_id}-{uuid.uuid4().hex[:8]}"
        status = "submitted"
        log.info(
            "WHCC stub submit order=%s ref=%s payload_items=%d",
            order_id,
            ref,
            len(payload["line_items"]),
        )

    db.update_order(order_id, lab_status=status, lab_ref=ref)
    db.insert_fulfillment_event(order_id, status=status, detail=f"whcc ref={ref}")
    return {"order_id": order_id, "lab_ref": ref, "lab_status": status}


def poll_order(order_id: int) -> dict[str, Any]:
    order = db.get_order(order_id)
    if not order:
        raise ValueError(f"order not found: {order_id}")
    current = order.get("lab_status") or ""
    if current in {"complete", "skipped", "canceled"}:
        return {"order_id": order_id, "lab_status": current, "advanced": False}

    if whcc_configured() and order.get("lab_ref"):
        url = f"{config.WHCC_API_URL.rstrip('/')}/orders/{order['lab_ref']}"
        headers = {"Authorization": f"Bearer {config.WHCC_API_KEY}"}
        with httpx.Client(timeout=15.0) as client:
            resp = client.get(url, headers=headers)
        if resp.status_code == 200:
            body = resp.json()
            remote = (body.get("status") or "").lower()
            mapped = {
                "received": "submitted",
                "in_production": "processing",
                "shipped": "shipped",
                "delivered": "complete",
            }.get(remote, current)
            if mapped != current:
                db.update_order(order_id, lab_status=mapped)
                db.insert_fulfillment_event(order_id, status=mapped, detail=f"whcc poll={remote}")
                from .lab import _notify_lab_transition

                _notify_lab_transition(order_id, mapped)
                return {"order_id": order_id, "lab_status": mapped, "advanced": True}
        return {"order_id": order_id, "lab_status": current, "advanced": False}

    next_status = _WHCC_FLOW.get(current)
    if not next_status:
        return {"order_id": order_id, "lab_status": current, "advanced": False}
    db.update_order(order_id, lab_status=next_status)
    db.insert_fulfillment_event(order_id, status=next_status, detail="whcc stub poll")
    from .lab import _notify_lab_transition

    _notify_lab_transition(order_id, next_status)
    return {"order_id": order_id, "lab_status": next_status, "advanced": True}


def handle_webhook(payload: dict[str, Any]) -> bool:
    """Apply WHCC shipment webhook to an order."""
    ref = payload.get("order_id") or payload.get("lab_ref")
    status = (payload.get("status") or "").lower()
    if not ref:
        return False
    order = db.get_order_by_lab_ref(str(ref))
    if not order:
        log.warning("WHCC webhook unknown ref %s", ref)
        return False
    mapped = {
        "received": "submitted",
        "in_production": "processing",
        "shipped": "shipped",
        "delivered": "complete",
    }.get(status)
    if not mapped:
        return False
    oid = int(order["id"])
    db.update_order(oid, lab_status=mapped)
    db.insert_fulfillment_event(oid, status=mapped, detail=f"whcc webhook={status}")
    return True