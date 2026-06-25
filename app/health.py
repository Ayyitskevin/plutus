"""Dependency checks for /healthz (studio / Mise feature mode)."""
from __future__ import annotations

from typing import Any

from . import config, db


def _check_database() -> dict[str, str]:
    try:
        if db.ping():
            return {"status": "ok", "backend": db.backend_name()}
        return {"status": "error", "detail": "ping failed", "backend": db.backend_name()}
    except Exception as exc:
        return {"status": "error", "detail": str(exc), "backend": db.backend_name()}


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


def build_health_report(*, worker: Any | None = None) -> dict:
    checks = {
        "database": _check_database(),
        "mise": _check_mise(),
        "argus": _check_argus(),
    }

    if checks["database"]["status"] == "error":
        overall = "error"
    elif any(item.get("status") == "error" for item in checks.values()):
        overall = "error"
    elif any(item.get("status") == "degraded" for item in checks.values()):
        overall = "degraded"
    else:
        overall = "ok"

    return {
        "status": overall,
        "checks": checks,
        "studio_mode": True,
    }