"""Optional Dionysus hand-off for client pitch copy."""
from __future__ import annotations

import logging
from typing import Any

import httpx

from . import config

log = logging.getLogger("plutus.dionysus")


class DionysusClientError(Exception):
    """Human-readable Dionysus API failure."""


def is_enabled() -> bool:
    return bool(config.DIONYSUS_URL and config.DIONYSUS_TOKEN and config.DIONYSUS_ORG_SLUG)


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {config.DIONYSUS_TOKEN}"}


def pitch_status() -> dict[str, Any]:
    if not is_enabled():
        return {"configured": False, "reachable": False}
    try:
        with httpx.Client(timeout=config.DIONYSUS_TIMEOUT) as client:
            resp = client.get(f"{config.DIONYSUS_URL}/readiness", headers=_headers())
        reachable = resp.status_code < 500
        return {"configured": True, "reachable": reachable, "org": config.DIONYSUS_ORG_SLUG}
    except httpx.HTTPError as exc:
        log.warning("Dionysus unreachable: %s", exc)
        return {"configured": True, "reachable": False, "detail": str(exc)}


def enhance_pitch(
    *,
    gallery_name: str,
    bundles: list[dict[str, Any]],
    estimated_total_cents: int,
    photo_count: int,
    gallery_theme: str | None = None,
    argus_run_id: int | None = None,
) -> dict[str, Any] | None:
    """Ask Dionysus for richer intro + bundle pitches; returns None when disabled."""
    if not is_enabled():
        return None
    payload = {
        "gallery_name": gallery_name,
        "photo_count": photo_count,
        "estimated_total_cents": estimated_total_cents,
        "gallery_theme": gallery_theme,
        "argus_run_id": argus_run_id,
        "bundles": [
            {
                "title": bundle.get("title"),
                "pitch": bundle.get("pitch"),
                "items": [
                    {
                        "label": item.get("label"),
                        "size": item.get("size"),
                        "photo": (item.get("photo") or {}).get("filename"),
                        "keywords": (item.get("photo") or {}).get("keywords"),
                    }
                    for item in (bundle.get("items") or [])
                ],
            }
            for bundle in bundles
        ],
    }
    url = (
        f"{config.DIONYSUS_URL.rstrip('/')}"
        f"/api/mise/organizations/{config.DIONYSUS_ORG_SLUG}/print-pitch"
    )
    try:
        with httpx.Client(timeout=config.DIONYSUS_TIMEOUT) as client:
            resp = client.post(url, json=payload, headers=_headers())
    except httpx.HTTPError as exc:
        log.warning("Dionysus pitch hand-off failed: %s", exc)
        return None
    if resp.status_code >= 400:
        log.warning("Dionysus pitch HTTP %s: %s", resp.status_code, resp.text[:200])
        return None
    body = resp.json()
    return body if isinstance(body, dict) else None