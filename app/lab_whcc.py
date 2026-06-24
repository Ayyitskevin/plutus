"""WHCC lab adapter — real API when configured, deterministic stub otherwise."""
from __future__ import annotations

import hashlib
import hmac
import logging
import time
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

_STATUS_MAP = {
    "received": "submitted",
    "in_production": "processing",
    "shipped": "shipped",
    "delivered": "complete",
}

_WHCC_RETRYABLE = {408, 429, 500, 502, 503, 504}


def whcc_configured() -> bool:
    return bool(config.WHCC_API_URL and config.WHCC_API_KEY)


def whcc_webhook_signature(payload: bytes, *, secret: str | None = None) -> str:
    key = (secret or config.WHCC_WEBHOOK_SECRET or "").encode("utf-8")
    return hmac.new(key, payload, hashlib.sha256).hexdigest()


def verify_webhook_signature(payload: bytes, sig_header: str | None) -> bool:
    secret = config.WHCC_WEBHOOK_SECRET
    if not secret or not sig_header:
        return False
    normalized = sig_header.strip()
    if normalized in {secret, f"Bearer {secret}"}:
        return True
    digest = normalized
    lower = normalized.lower()
    if lower.startswith("sha256="):
        digest = normalized.split("=", 1)[1].strip()
    elif "v1=" in normalized:
        parts: dict[str, str] = {}
        for item in normalized.split(","):
            key, _, value = item.partition("=")
            parts[key.strip()] = value.strip()
        digest = parts.get("v1", "")
    if len(digest) != 64:
        return False
    expected = whcc_webhook_signature(payload, secret=secret)
    return hmac.compare_digest(expected, digest.lower())


def verify_webhook_token(token: str) -> bool:
    """Legacy static-token check (no body). Prefer verify_webhook_signature."""
    secret = config.WHCC_WEBHOOK_SECRET
    if not secret:
        return False
    normalized = (token or "").strip()
    return normalized in {secret, f"Bearer {secret}"}


def _map_status(remote: str, *, fallback: str = "") -> str | None:
    return _STATUS_MAP.get((remote or "").lower()) or (fallback or None)


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {config.WHCC_API_KEY}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def _request(method: str, path: str, *, json_body: dict | None = None) -> httpx.Response:
    if not whcc_configured():
        raise RuntimeError("WHCC is not configured")
    url = f"{config.WHCC_API_URL.rstrip('/')}{path}"
    attempts = max(1, int(getattr(config, "WHCC_RETRY_ATTEMPTS", 3)))
    last_exc: Exception | None = None
    with httpx.Client(timeout=30.0) as client:
        for attempt in range(1, attempts + 1):
            try:
                resp = client.request(method, url, json=json_body, headers=_headers())
            except httpx.HTTPError as exc:
                last_exc = exc
                log.warning("WHCC %s %s attempt %s failed: %s", method, path, attempt, exc)
                if attempt < attempts:
                    time.sleep(min(2 ** attempt, 8))
                continue
            if resp.status_code in _WHCC_RETRYABLE and attempt < attempts:
                log.warning(
                    "WHCC %s %s retryable HTTP %s (attempt %s)",
                    method,
                    path,
                    resp.status_code,
                    attempt,
                )
                time.sleep(min(2 ** attempt, 8))
                continue
            return resp
    raise RuntimeError(f"WHCC unreachable: {last_exc}") from last_exc


def _order_payload(order: dict) -> dict[str, Any]:
    items = []
    image_urls: list[str] = []
    for line in order.get("items") or []:
        row = {
            "sku": line["sku"],
            "description": line["label"],
            "quantity": line["quantity"],
            "unit_price_cents": line["unit_cents"],
        }
        image_url = line.get("image_url") or line.get("preview_url")
        if image_url:
            row["image_url"] = image_url
            image_urls.append(str(image_url))
        items.append(row)
    payload: dict[str, Any] = {
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
    if image_urls:
        payload["image_urls"] = image_urls
    ship = order.get("shipping") if isinstance(order.get("shipping"), dict) else None
    if ship:
        payload["shipping_address"] = ship
    return payload


def submit_order(order_id: int) -> dict[str, Any]:
    order = db.get_order(order_id)
    if not order:
        raise ValueError(f"order not found: {order_id}")

    payload = _order_payload(order)
    if whcc_configured():
        resp = _request("POST", "/orders", json_body=payload)
        if resp.status_code >= 400:
            log.error("WHCC submit failed %s: %s", resp.status_code, resp.text[:300])
            raise RuntimeError(f"WHCC HTTP {resp.status_code}")
        body = resp.json()
        ref = body.get("order_id") or body.get("id") or f"whcc-{order_id}"
        status = _map_status(body.get("status") or "", fallback="submitted") or "submitted"
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
        resp = _request("GET", f"/orders/{order['lab_ref']}")
        if resp.status_code == 200:
            body = resp.json()
            remote = (body.get("status") or "").lower()
            mapped = _map_status(remote, fallback=current)
            if mapped and mapped != current:
                db.update_order(order_id, lab_status=mapped)
                db.insert_fulfillment_event(
                    order_id, status=mapped, detail=f"whcc poll={remote}"
                )
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
    mapped = _map_status(status)
    if not mapped:
        return False
    oid = int(order["id"])
    db.update_order(oid, lab_status=mapped)
    db.insert_fulfillment_event(oid, status=mapped, detail=f"whcc webhook={status}")
    return True


def whcc_status() -> dict[str, Any]:
    if not whcc_configured():
        return {"configured": False, "reachable": False}
    try:
        resp = _request("GET", "/health")
        reachable = resp.status_code < 500
        return {
            "configured": True,
            "reachable": reachable,
            "detail": None if reachable else f"HTTP {resp.status_code}",
        }
    except Exception as exc:
        log.warning("WHCC health check failed: %s", exc)
        return {"configured": True, "reachable": False, "detail": str(exc)}