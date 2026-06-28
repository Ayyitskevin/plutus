"""In-process counters for Plutus ops."""
from __future__ import annotations

import time
from threading import Lock

_lock = Lock()
_started_at = time.time()
_counters: dict[str, int] = {
    "recommend_folder": 0,
    "recommend_mise": 0,
}
_gauges: dict[str, float] = {}


def inc(name: str, amount: int = 1) -> None:
    with _lock:
        _counters[name] = _counters.get(name, 0) + amount


def set_gauge(name: str, value: float) -> None:
    with _lock:
        _gauges[name] = value


def snapshot() -> dict:
    with _lock:
        return {
            "uptime_seconds": round(time.time() - _started_at, 1),
            "counters": dict(_counters),
        }


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
    return "\n".join(lines) + "\n"