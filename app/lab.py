"""Lab fulfillment adapter — mock today, WHCC/Miller's later."""
from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from . import config, db

log = logging.getLogger("plutus.lab")

_MOCK_FLOW = ("submitted", "processing", "shipped", "complete")


class LabError(Exception):
    """Fulfillment submission failure."""


def lab_enabled() -> bool:
    return config.LAB_ADAPTER not in {"", "disabled", "none"}


def _record_transition(order_id: int, status: str, *, detail: str | None = None) -> None:
    db.insert_fulfillment_event(order_id, status=status, detail=detail)


def submit_order(order_id: int) -> dict[str, Any]:
    """Submit a paid order to the configured lab adapter."""
    order = db.get_order(order_id)
    if not order:
        raise LabError(f"order not found: {order_id}")
    if order.get("lab_status") in {"submitted", "processing", "shipped", "complete"}:
        return {
            "order_id": order_id,
            "lab_ref": order.get("lab_ref"),
            "lab_status": order["lab_status"],
            "skipped": True,
        }

    if not lab_enabled():
        db.update_order(order_id, lab_status="skipped")
        _record_transition(order_id, "skipped", detail="lab adapter disabled")
        return {"order_id": order_id, "lab_status": "skipped", "skipped": True}

    if config.LAB_ADAPTER == "mock":
        ref = f"mock-{order_id}-{uuid.uuid4().hex[:8]}"
        db.update_order(order_id, lab_status="submitted", lab_ref=ref)
        _record_transition(order_id, "submitted", detail=f"ref={ref}")
        log.info("mock lab submission order=%s ref=%s", order_id, ref)
        return {"order_id": order_id, "lab_ref": ref, "lab_status": "submitted"}

    if config.LAB_ADAPTER == "whcc":
        from . import lab_whcc

        try:
            return lab_whcc.submit_order(order_id)
        except Exception as exc:
            raise LabError(str(exc)) from exc

    raise LabError(f"unknown lab adapter: {config.LAB_ADAPTER}")


def _seconds_since(iso_ts: str | None) -> float:
    if not iso_ts:
        return 0.0
    try:
        dt = datetime.fromisoformat(iso_ts.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return max(0.0, (datetime.now(UTC) - dt).total_seconds())
    except ValueError:
        return 0.0


def _mock_next_status(current: str, elapsed: float) -> str | None:
    if current == "submitted" and elapsed >= config.LAB_MOCK_PROCESS_SECONDS:
        return "processing"
    if current == "processing" and elapsed >= config.LAB_MOCK_SHIP_SECONDS:
        return "shipped"
    if current == "shipped" and elapsed >= config.LAB_MOCK_SHIP_SECONDS + 300:
        return "complete"
    return None


def poll_order(order_id: int) -> dict[str, Any]:
    """Advance mock lab status based on elapsed time since payment."""
    order = db.get_order(order_id)
    if not order:
        raise LabError(f"order not found: {order_id}")
    if not lab_enabled():
        return {"order_id": order_id, "lab_status": order.get("lab_status"), "advanced": False}

    if config.LAB_ADAPTER == "whcc":
        from . import lab_whcc

        return lab_whcc.poll_order(order_id)

    if config.LAB_ADAPTER != "mock":
        return {"order_id": order_id, "lab_status": order.get("lab_status"), "advanced": False}

    current = order.get("lab_status") or ""
    if current not in _MOCK_FLOW:
        return {"order_id": order_id, "lab_status": current, "advanced": False}

    elapsed = _seconds_since(order.get("paid_at") or order.get("created_at"))
    next_status = _mock_next_status(current, elapsed)
    if not next_status:
        return {"order_id": order_id, "lab_status": current, "advanced": False}

    db.update_order(order_id, lab_status=next_status)
    _record_transition(order_id, next_status, detail=f"mock poll after {int(elapsed)}s")
    log.info("mock lab poll order=%s %s -> %s", order_id, current, next_status)
    _notify_lab_transition(order_id, next_status)
    return {"order_id": order_id, "lab_status": next_status, "advanced": True}


def _notify_lab_transition(order_id: int, status: str) -> None:
    from . import notifications

    try:
        notifications.notify_lab_status(order_id, status)
    except Exception:
        log.exception("lab status notification failed order=%s status=%s", order_id, status)


def poll_pending_orders(*, limit: int = 50) -> int:
    """Poll all in-flight mock orders; returns count advanced."""
    advanced = 0
    for row in db.list_orders_pending_lab_poll(limit=limit):
        result = poll_order(int(row["id"]))
        if result.get("advanced"):
            advanced += 1
    return advanced


def fulfillment_status() -> dict:
    from . import lab_whcc

    out = {
        "enabled": lab_enabled(),
        "adapter": config.LAB_ADAPTER,
        "mock_process_seconds": config.LAB_MOCK_PROCESS_SECONDS,
        "mock_ship_seconds": config.LAB_MOCK_SHIP_SECONDS,
    }
    if config.LAB_ADAPTER == "whcc":
        out["whcc_configured"] = lab_whcc.whcc_configured()
    return out