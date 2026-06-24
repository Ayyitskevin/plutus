"""Photographer-facing order display helpers."""
from __future__ import annotations

from typing import Any

from .gallery_media import photo_filename


def bundle_for_order(order: dict[str, Any], run: dict[str, Any] | None) -> dict[str, Any] | None:
    if not run:
        return None
    bundles = (run.get("payload") or {}).get("bundles") or []
    idx = int(order.get("bundle_index") or 0)
    if 0 <= idx < len(bundles):
        return bundles[idx]
    return None


def bundle_title_for_order(order: dict[str, Any], run: dict[str, Any] | None) -> str | None:
    bundle = bundle_for_order(order, run)
    if not bundle:
        return None
    title = bundle.get("title")
    return str(title) if title else None


def enrich_order_bundle(
    order: dict[str, Any],
    run: dict[str, Any] | None,
    *,
    photo_base: str,
) -> dict[str, Any]:
    bundle = bundle_for_order(order, run)
    if not bundle:
        return {"title": None, "pitch": None, "line_items": [], "hero_thumb_url": None}

    bundle_items = bundle.get("items") or []
    items_out: list[dict[str, Any]] = []
    for idx, line in enumerate(order.get("items") or []):
        row = dict(line)
        bundle_line = bundle_items[idx] if idx < len(bundle_items) else {}
        fname = photo_filename(bundle_line.get("photo"))
        if fname:
            row["thumb_url"] = f"{photo_base}/{fname}"
        items_out.append(row)

    hero = items_out[0].get("thumb_url") if items_out else None
    return {
        "title": bundle.get("title"),
        "pitch": bundle.get("pitch"),
        "line_items": items_out,
        "hero_thumb_url": hero,
    }