"""Dependency checks for /healthz."""
from __future__ import annotations

from typing import Any

from . import billing, config, db, homelab


def _check_database() -> dict[str, str]:
    try:
        if db.ping():
            return {"status": "ok"}
        return {"status": "error", "detail": "ping failed"}
    except Exception as exc:
        return {"status": "error", "detail": str(exc)}


def _check_store_billing() -> dict[str, str | bool]:
    """Client bundle checkout Stripe (homelab storefront)."""
    stripe = billing.stripe_connectivity()
    if not stripe.get("configured"):
        return {"status": "disabled", "configured": False, "reachable": False}
    reachable = bool(stripe.get("reachable"))
    return {
        "status": "ok" if reachable else "degraded",
        "configured": True,
        "reachable": reachable,
        "test_mode": stripe.get("test_mode"),
        "simulate_payment": config.ALLOW_SIMULATE_PAYMENT,
    }


def _check_billing() -> dict[str, str | bool]:
    if not config.SAAS_MODE:
        return {"status": "disabled"}
    stripe = billing.stripe_connectivity()
    if not stripe.get("configured"):
        return {"status": "disabled", "configured": False, "webhook": False}
    reachable = bool(stripe.get("reachable"))
    return {
        "status": "ok" if reachable else "degraded",
        "configured": True,
        "reachable": reachable,
        "test_mode": stripe.get("test_mode"),
        "webhook": bool(config.STRIPE_WEBHOOK_SECRET),
    }


def _check_storage() -> dict[str, str | bool]:
    from . import storage

    st = storage.storage_status()
    return {
        "status": "ok" if st.get("configured") else "degraded",
        **st,
    }


def _check_mise() -> dict[str, str | bool]:
    from . import mise_client

    configured = mise_client.is_enabled()
    return {"status": "ok" if configured else "disabled", "configured": configured}


def _check_argus() -> dict[str, str | bool]:
    from . import argus_client

    if not argus_client.is_enabled():
        return {"status": "disabled", "configured": False}
    st = argus_client.vision_status()
    reachable = bool(st.get("reachable"))
    return {
        "status": "ok" if reachable else "degraded",
        "configured": True,
        "auto_vision": config.ARGUS_AUTO_VISION,
        **{k: v for k, v in st.items() if k not in {"configured", "reachable"}},
    }


def _check_lab() -> dict[str, str | bool]:
    from . import lab

    return {
        "status": "ok" if lab.lab_enabled() else "disabled",
        "adapter": config.LAB_ADAPTER,
        "enabled": lab.lab_enabled(),
    }


def _check_upload_worker() -> dict[str, str | int | bool]:
    queued = len(db.list_upload_batches_by_status("queued", limit=100))
    analyzing = len(db.list_upload_batches_by_status("analyzing", limit=100))
    return {
        "status": "ok" if config.UPLOAD_ASYNC_ANALYZE else "disabled",
        "enabled": config.UPLOAD_ASYNC_ANALYZE,
        "interval_seconds": config.UPLOAD_WORKER_INTERVAL,
        "queued": queued,
        "analyzing": analyzing,
    }


def _check_notifications() -> dict[str, str | bool]:
    smtp = bool(config.SMTP_HOST and config.SMTP_FROM)
    return {
        "status": "ok" if smtp or config.ORDER_WEBHOOK_URL else "disabled",
        "smtp": smtp,
        "webhook": bool(config.ORDER_WEBHOOK_URL),
    }


def build_health_report(*, worker: Any | None = None) -> dict:
    checks = {
        "database": _check_database(),
        "mise": _check_mise(),
    }
    if config.SAAS_MODE:
        checks["storage"] = _check_storage()
        checks["argus"] = _check_argus()
        checks["billing"] = _check_billing()
        checks["lab"] = _check_lab()
        checks["upload_worker"] = _check_upload_worker()
        checks["notifications"] = _check_notifications()
    elif homelab.store_enabled():
        checks["billing"] = _check_store_billing()
        checks["lab"] = _check_lab()

    if checks["database"]["status"] == "error":
        overall = "error"
    elif any(item.get("status") == "degraded" for item in checks.values()):
        overall = "degraded"
    else:
        overall = "ok"

    return {
        "status": overall,
        "checks": checks,
        "saas_mode": config.SAAS_MODE,
        "homelab_store": homelab.store_enabled(),
    }