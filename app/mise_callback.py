"""Hardened offer callback delivery to Mise.

Pushes the finished offer to Mise's ``/api/plutus/callback`` with three guarantees:

1. **Idempotency** — a stable ``Idempotency-Key`` per ``(gallery_id, run_id)`` (run_id
   is already stable per gallery), sent as a header and a body field, so a re-run or
   re-delivery never duplicates an offer or double-triggers anything downstream.
2. **Auth** — on HTTP 401 the bearer is refreshed from ``.env`` (picks up a rotated
   secret with no restart) and retried once; a hard auth failure is dead-lettered and
   surfaced (``log.error``), never silently dropping a completed recommendation.
3. **Delivery** — transient failures (transport errors, timeouts, 5xx) retry with
   exponential backoff up to ``MISE_CALLBACK_MAX_ATTEMPTS``, then the offer is
   dead-lettered locally (persisted, re-deliverable) rather than lost.

``deliver()`` NEVER raises — a callback failure must not crash Mise's recommend path.
Default OFF (``callback_enabled``); the synchronous /recommend response is the live
contract.
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any

import httpx
from dotenv import load_dotenv

from . import config, db

log = logging.getLogger("plutus.callback")


def idempotency_key(gallery_id: int, run_id: int | None) -> str:
    """Stable key per (gallery_id, run). Re-runs/re-deliveries reuse it verbatim."""
    return f"plutus-offer-{gallery_id}-{run_id}"


def _base() -> str | None:
    return (config.MISE_CALLBACK_URL or config.MISE_URL).rstrip("/") or None


def _token() -> str | None:
    return config.MISE_CALLBACK_TOKEN or config.MISE_API_TOKEN or None


def callback_enabled() -> bool:
    """True only when opted in AND a target + token are configured."""
    return bool(config.MISE_CALLBACK_ENABLED and _base() and _token())


def _refresh_callback_token() -> str | None:
    """Re-read a rotated bearer from .env without a restart, updating live config."""
    load_dotenv(config._ROOT / ".env", override=True)
    config.MISE_CALLBACK_TOKEN = os.environ.get("PLUTUS_MISE_CALLBACK_TOKEN") or None
    config.MISE_API_TOKEN = os.environ.get("PLUTUS_MISE_API_TOKEN", "")
    return _token()


def _alert(idem: str, last: dict[str, Any]) -> None:
    """Surface a hard failure. Logged at error level so ops/monitoring catch it."""
    log.error(
        "Mise callback dead-lettered: key=%s last_status=%s detail=%s",
        idem,
        last.get("outcome"),
        last.get("http_status") or last.get("detail"),
    )


def _post_once(
    *, url: str, gallery_id: int, body: dict[str, Any], token: str | None, idem: str
) -> dict[str, Any]:
    """Single delivery attempt. Classifies the result; never raises."""
    headers = {"Authorization": f"Bearer {token}", "Idempotency-Key": idem}
    try:
        with httpx.Client(timeout=config.MISE_TIMEOUT) as client:
            resp = client.post(
                url, params={"gallery_id": gallery_id}, json=body, headers=headers
            )
    except Exception as exc:  # noqa: BLE001 — transport failure is transient, retry it
        return {"outcome": "transport_error", "detail": str(exc)[:200]}
    sc = resp.status_code
    if sc in (404, 410):
        return {"outcome": "ignored", "http_status": sc}  # unknown subject = no-op
    if sc == 401:
        return {"outcome": "unauthorized", "http_status": sc}
    if 500 <= sc < 600:
        return {"outcome": "server_error", "http_status": sc}
    if sc >= 400:
        return {"outcome": "client_error", "http_status": sc}
    return {"outcome": "delivered", "http_status": sc}


def deliver(
    *,
    gallery_id: int,
    run_id: int | None,
    payload: dict[str, Any],
    correlation_id: str | None = None,
) -> dict[str, Any]:
    """Deliver the offer with retry/refresh/dead-letter. Never raises."""
    if not callback_enabled():
        return {"status": "disabled"}

    idem = idempotency_key(gallery_id, run_id)
    url = f"{_base()}/api/plutus/callback"
    body = dict(payload)
    body["idempotency_key"] = idem
    if correlation_id:
        body["correlation_id"] = correlation_id

    token = _token()
    max_attempts = max(1, config.MISE_CALLBACK_MAX_ATTEMPTS)
    auth_retry_left = True
    attempt = 0
    last: dict[str, Any] = {}

    while attempt < max_attempts:
        attempt += 1
        last = _post_once(url=url, gallery_id=gallery_id, body=body, token=token, idem=idem)
        outcome = last["outcome"]

        if outcome in ("delivered", "ignored"):
            db.delete_callback_deadletter(idem)  # clear any prior dead-letter for this run
            return {
                "status": outcome,
                "idempotency_key": idem,
                "attempts": attempt,
                "http_status": last.get("http_status"),
            }

        if outcome == "unauthorized" and auth_retry_left:
            # Rotated token? Refresh from .env and grant one bonus attempt.
            auth_retry_left = False
            token = _refresh_callback_token()
            max_attempts += 1
            log.warning("Mise callback 401 for %s — refreshed token, retrying", idem)
            continue

        if outcome in ("transport_error", "server_error") and attempt < max_attempts:
            time.sleep(config.MISE_CALLBACK_BACKOFF_BASE * (2 ** (attempt - 1)))
            continue

        break

    # Exhausted retries or a hard failure → persist (re-deliverable) and surface.
    status = "auth_failed" if last.get("outcome") == "unauthorized" else "dead_lettered"
    db.upsert_callback_deadletter(
        idempotency_key=idem,
        gallery_id=gallery_id,
        run_id=run_id,
        payload=body,
        correlation_id=correlation_id,
        attempts=attempt,
        last_status=last.get("outcome"),
        last_error=str(last.get("http_status") or last.get("detail") or ""),
    )
    _alert(idem, last)
    return {
        "status": status,
        "idempotency_key": idem,
        "attempts": attempt,
        "http_status": last.get("http_status"),
        "detail": last.get("detail"),
    }


def redeliver_pending(*, limit: int = 50) -> dict[str, Any]:
    """Re-attempt dead-lettered offers. A success clears its outbox row."""
    rows = db.list_callback_deadletter(limit=limit)
    results = []
    for row in rows:
        out = deliver(
            gallery_id=int(row["gallery_id"]),
            run_id=row.get("run_id"),
            payload=row["payload"],
            correlation_id=row.get("correlation_id"),
        )
        results.append({"idempotency_key": row["idempotency_key"], "status": out["status"]})
    return {"attempted": len(rows), "results": results}
