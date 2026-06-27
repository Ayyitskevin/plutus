"""Read-only Mise gallery index (same bearer as MISE_ARGUS_TOKEN on flow)."""
from __future__ import annotations

import logging
from typing import Any

import httpx

from . import config

log = logging.getLogger("plutus.mise")


class MiseClientError(Exception):
    """Human-readable Mise API failure."""


def is_enabled() -> bool:
    return bool(config.MISE_URL and config.MISE_API_TOKEN)


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {config.MISE_API_TOKEN}"}


def list_galleries(*, published: bool | None = None) -> dict[str, Any]:
    if not is_enabled():
        raise MiseClientError("Mise API is not configured")
    url = f"{config.MISE_URL}/api/galleries"
    params: dict[str, str] = {}
    if published is not None:
        params["published"] = "true" if published else "false"
    try:
        with httpx.Client(timeout=config.MISE_TIMEOUT) as client:
            resp = client.get(url, params=params or None, headers=_headers())
    except httpx.TimeoutException as exc:
        raise MiseClientError(f"Mise API timed out: {exc}") from exc
    except httpx.RequestError as exc:
        raise MiseClientError(f"Mise API unreachable: {exc}") from exc

    if resp.status_code == 503:
        raise MiseClientError("Mise galleries API is disarmed")
    if resp.status_code == 401:
        raise MiseClientError("Mise API rejected the bearer token")
    if resp.status_code >= 400:
        raise MiseClientError(f"Mise API returned HTTP {resp.status_code}")

    body = resp.json()
    if not isinstance(body, dict) or "galleries" not in body:
        raise MiseClientError("Mise API returned an unexpected body")
    return body


def get_gallery(gallery_id: int) -> dict[str, Any] | None:
    body = list_galleries(published=False)
    for row in body.get("galleries") or []:
        if row.get("id") == gallery_id:
            return row
    return None


def _callback_base() -> str | None:
    return (config.MISE_CALLBACK_URL or config.MISE_URL).rstrip("/") or None


def _callback_token() -> str | None:
    return config.MISE_CALLBACK_TOKEN or config.MISE_API_TOKEN or None


def callback_enabled() -> bool:
    """True only when the async push is opted in AND a target + token are set."""
    return bool(config.MISE_CALLBACK_ENABLED and _callback_base() and _callback_token())


def post_offer_callback(
    *,
    gallery_id: int,
    payload: dict[str, Any],
    correlation_id: str | None = None,
) -> dict[str, Any]:
    """Push the finished offer to Mise's /api/plutus/callback. NEVER raises.

    Returns a status dict for observability. Resilience contract:
    - A callback for an unknown subject (HTTP 404/410) is a no-op, not an error.
    - Any transport or HTTP failure is swallowed and recorded — a Plutus callback
      failure must never crash Mise's publish/recommend path.
    Echoes ``correlation_id`` in the body when provided.
    """
    if not callback_enabled():
        return {"status": "disabled"}
    base = _callback_base()
    token = _callback_token()
    url = f"{base}/api/plutus/callback"
    body = dict(payload)
    if correlation_id:
        body["correlation_id"] = correlation_id
    try:
        with httpx.Client(timeout=config.MISE_TIMEOUT) as client:
            resp = client.post(
                url,
                params={"gallery_id": gallery_id},
                json=body,
                headers={"Authorization": f"Bearer {token}"},
            )
    except Exception as exc:  # noqa: BLE001 — resilience: never propagate
        log.warning("Mise callback transport failure for gallery %s: %s", gallery_id, exc)
        return {"status": "error", "detail": str(exc)[:200]}
    if resp.status_code in (404, 410):
        # Unknown subject — Mise doesn't recognize this gallery; treat as a no-op.
        return {"status": "ignored", "http_status": resp.status_code}
    if resp.status_code >= 400:
        log.warning("Mise callback rejected for gallery %s: HTTP %s", gallery_id, resp.status_code)
        return {"status": "error", "http_status": resp.status_code}
    return {"status": "delivered", "http_status": resp.status_code}