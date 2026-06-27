"""In-process counters for Plutus ops."""
from __future__ import annotations

import time
from threading import Lock

_lock = Lock()
_started_at = time.time()
_counters: dict[str, int] = {
    "recommend_folder": 0,
    "recommend_mise": 0,
    "rate_limit_exceeded": 0,
}
_gauges: dict[str, float] = {}
_tenant_counters: dict[str, dict[str, int]] = {}


def inc(name: str, amount: int = 1) -> None:
    with _lock:
        _counters[name] = _counters.get(name, 0) + amount


def set_gauge(name: str, value: float) -> None:
    with _lock:
        _gauges[name] = value


def inc_tenant(tenant_id: str | None, name: str, amount: int = 1) -> None:
    if not tenant_id:
        return
    with _lock:
        bucket = _tenant_counters.setdefault(tenant_id, {})
        bucket[name] = bucket.get(name, 0) + amount


def snapshot(*, tenant_id: str | None = None) -> dict:
    with _lock:
        out = {
            "uptime_seconds": round(time.time() - _started_at, 1),
            "counters": dict(_counters),
        }
        if tenant_id:
            out["tenant_counters"] = dict(_tenant_counters.get(tenant_id, {}))
        elif _tenant_counters:
            out["by_tenant"] = {tid: dict(vals) for tid, vals in _tenant_counters.items()}
        return out


def prometheus_text() -> str:
    snap = snapshot()
    with _lock:
        gauges = dict(_gauges)
    lines = [
        "# HELP plutus_uptime_seconds Process uptime in seconds.",
        "# TYPE plutus_uptime_seconds gauge",
        f"plutus_uptime_seconds {snap['uptime_seconds']}",
    ]
    for name, value in sorted(gauges.items()):
        metric = f"plutus_{name}"
        lines.extend(
            [
                f"# HELP {metric} Plutus gauge {name}.",
                f"# TYPE {metric} gauge",
                f"{metric} {value}",
            ]
        )
    for name, value in sorted(snap["counters"].items()):
        metric = f"plutus_{name}_total"
        lines.extend(
            [
                f"# HELP {metric} Plutus counter {name}.",
                f"# TYPE {metric} counter",
                f"{metric} {value}",
            ]
        )
    for tenant_id, counters in sorted(snap.get("by_tenant", {}).items()):
        for name, value in sorted(counters.items()):
            metric = f"plutus_tenant_{name}_total"
            lines.extend(
                [
                    f"# HELP {metric} Per-tenant counter {name}.",
                    f"# TYPE {metric} counter",
                    f'{metric}{{tenant_id="{tenant_id}"}} {value}',
                ]
            )
    return "\n".join(lines) + "\n"