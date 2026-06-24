"""Edit recommendation bundles before sharing with clients."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from . import db, ingest, orders, recommend

log = logging.getLogger("plutus.bundle_editor")


class BundleEditError(Exception):
    """Invalid bundle edit payload."""


def _photo_index(photos: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    by_name: dict[str, dict[str, Any]] = {}
    for photo in photos:
        name = photo.get("filename")
        if name:
            by_name[str(name)] = photo
    return by_name


def photos_for_run(run: dict[str, Any]) -> list[dict[str, Any]]:
    """Gallery frames available for bundle photo swaps."""
    gallery = db.get_gallery(int(run["gallery_id"]))
    photos: list[dict[str, Any]] = []
    if gallery and gallery.get("source"):
        folder = Path(str(gallery["source"]))
        if folder.is_dir():
            try:
                photos = ingest.photos_from_folder(folder)
            except (FileNotFoundError, OSError) as exc:
                log.warning("gallery folder unreadable for run %s: %s", run.get("id"), exc)

    indexed = _photo_index(photos)
    payload = run.get("payload") or {}
    for row in payload.get("top_photos") or []:
        name = row.get("filename")
        if name and name not in indexed:
            indexed[str(name)] = {
                "filename": str(name),
                "path": None,
                "keeper_score": None,
                "hero_potential": None,
                "shot_type": row.get("shot_type"),
                "keywords": [],
            }
    for bundle in payload.get("bundles") or []:
        for item in bundle.get("items") or []:
            photo = item.get("photo") or {}
            name = photo.get("filename")
            if name and name not in indexed:
                indexed[str(name)] = dict(photo)
    return sorted(indexed.values(), key=lambda p: str(p.get("filename") or ""))


def _enabled_bundles(bundles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [b for b in bundles if b.get("enabled", True) and b.get("items")]


def estimated_total_cents(bundles: list[dict[str, Any]], tenant_id: str | None) -> int:
    total = 0
    for bundle in _enabled_bundles(bundles):
        total += orders.bundle_total_cents(bundle, tenant_id or "")
    return total


def apply_edits(
    *,
    run: dict[str, Any],
    tenant_id: str | None,
    bundle_edits: list[dict[str, Any]],
) -> dict[str, Any]:
    payload = dict(run.get("payload") or {})
    bundles = list(payload.get("bundles") or [])
    if len(bundle_edits) != len(bundles):
        raise BundleEditError("bundle count mismatch")

    photos = _photo_index(photos_for_run(run))

    updated: list[dict[str, Any]] = []
    for bundle, edit in zip(bundles, bundle_edits, strict=True):
        row = dict(bundle)
        row["title"] = (edit.get("title") or row.get("title") or "").strip() or row.get("title")
        row["pitch"] = (edit.get("pitch") or "").strip()
        row["enabled"] = bool(edit.get("enabled", True))

        items_out = []
        item_edits = edit.get("items") or []
        for idx, item in enumerate(row.get("items") or []):
            line = dict(item)
            if idx < len(item_edits):
                chosen = item_edits[idx].get("photo_filename")
                if chosen:
                    photo = photos.get(str(chosen))
                    if not photo:
                        raise BundleEditError(f"photo not in gallery: {chosen}")
                    line = recommend.refresh_item_photo(line, photo, tenant_id=tenant_id)
            items_out.append(line)
        row["items"] = items_out

        slot_edits = edit.get("photo_slots")
        if slot_edits is not None:
            slots = []
            for name in slot_edits:
                if name and str(name) not in photos:
                    raise BundleEditError(f"photo not in gallery: {name}")
                if name:
                    slots.append(str(name))
            row["photo_slots"] = slots

        updated.append(row)

    payload["bundles"] = updated
    payload["estimated_total_cents"] = estimated_total_cents(updated, tenant_id)
    return payload


def parse_bundle_form(form: Any) -> list[dict[str, Any]]:
    """Parse multipart form keys b{N}_* into bundle edit structs."""
    import re

    bundle_idxs: set[int] = set()
    for key in form.keys():
        match = re.match(r"^b(\d+)_", key)
        if match:
            bundle_idxs.add(int(match.group(1)))
    if not bundle_idxs:
        raise BundleEditError("no bundle fields in form")

    edits: list[dict[str, Any]] = []
    for bi in sorted(bundle_idxs):
        prefix = f"b{bi}_"
        enabled_val = form.get(f"{prefix}enabled")
        enabled = enabled_val in ("on", "true", "1", True)
        items: list[dict[str, Any]] = []
        item_idxs: set[int] = set()
        for key in form.keys():
            match = re.match(rf"^b{bi}_item(\d+)_photo$", key)
            if match:
                item_idxs.add(int(match.group(1)))
        for ii in sorted(item_idxs):
            photo = form.get(f"b{bi}_item{ii}_photo")
            if photo:
                items.append({"photo_filename": str(photo)})
        slots_raw = form.get(f"{prefix}photo_slots")
        slot_list = None
        if slots_raw is not None:
            slot_list = [s.strip() for s in str(slots_raw).split(",") if s.strip()]
        edits.append({
            "title": form.get(f"{prefix}title"),
            "pitch": form.get(f"{prefix}pitch"),
            "enabled": enabled,
            "items": items,
            "photo_slots": slot_list,
        })
    return edits


def save_run_edits(
    *,
    run_id: int,
    tenant_id: str | None,
    bundle_edits: list[dict[str, Any]],
) -> dict[str, Any]:
    run = db.get_run(run_id, tenant_id=tenant_id)
    if not run:
        raise BundleEditError("run not found")
    payload = apply_edits(run=run, tenant_id=tenant_id, bundle_edits=bundle_edits)
    enabled = _enabled_bundles(payload.get("bundles") or [])
    if not enabled:
        raise BundleEditError("at least one bundle must stay enabled")
    db.update_run(
        run_id,
        tenant_id=tenant_id,
        bundle_count=len(enabled),
        estimated_total_cents=int(payload.get("estimated_total_cents") or 0),
        payload=payload,
    )
    return payload