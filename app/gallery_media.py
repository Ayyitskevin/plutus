"""Serve gallery photos on client offer pages — token-scoped, thumbnail cache."""
from __future__ import annotations

import hashlib
import io
import logging
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps

from . import config, storage

log = logging.getLogger("plutus.gallery_media")

THUMB_MAX_EDGE = 520
FULL_MAX_EDGE = 1600


class GalleryMediaError(Exception):
    """Photo lookup or render failure."""


def _safe_filename(name: str) -> str:
    base = Path(name or "").name
    if not base or base in {".", ".."} or base != name.strip():
        raise GalleryMediaError("invalid filename")
    return base


def filenames_in_run(payload: dict[str, Any]) -> set[str]:
    names: set[str] = set()
    for bundle in payload.get("bundles") or []:
        for item in bundle.get("items") or []:
            photo = item.get("photo") or {}
            if photo.get("filename"):
                names.add(str(photo["filename"]))
        for slot in bundle.get("photo_slots") or []:
            if slot:
                names.add(str(slot))
    for photo in payload.get("top_photos") or []:
        if photo.get("filename"):
            names.add(str(photo["filename"]))
    return names


def offer_photo_path(
    store_slug: str,
    token: str,
    filename: str,
    *,
    size: str = "thumb",
) -> str:
    safe = _safe_filename(filename)
    base = f"/store/{store_slug}/offer/{token}/photo/{safe}"
    if size == "full":
        return f"{base}?size=full"
    return base


def resolve_photo_file(
    *,
    gallery: dict[str, Any] | None,
    payload: dict[str, Any],
    filename: str,
) -> Path:
    safe = _safe_filename(filename)
    if safe not in filenames_in_run(payload):
        raise GalleryMediaError("photo not in this offer")

    path_hint: str | None = None
    for bundle in payload.get("bundles") or []:
        for item in bundle.get("items") or []:
            photo = item.get("photo") or {}
            if photo.get("filename") == safe and photo.get("path"):
                path_hint = str(photo["path"])
                break
        if path_hint:
            break

    candidates: list[Path] = []
    if path_hint:
        if path_hint.startswith("s3://"):
            without = path_hint.removeprefix("s3://")
            _bucket, _, key = without.partition("/")
            cache = config.DATA_DIR / "offer_cache" / hashlib.sha256(path_hint.encode()).hexdigest()[:16]
            cache.mkdir(parents=True, exist_ok=True)
            candidates.append(storage._materialize_s3_uri(path_hint, cache))
        else:
            candidates.append(Path(path_hint))

    if gallery:
        source = gallery.get("source")
        if source:
            src = Path(str(source))
            if src.is_dir():
                candidates.append(src / safe)
            elif src.is_file():
                candidates.append(src)
        mise_id = gallery.get("mise_gallery_id")
        if mise_id and config.MISE_MEDIA_ROOT:
            candidates.append(
                config.MISE_MEDIA_ROOT / str(mise_id) / "original" / safe
            )

    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate)
        if key in seen:
            continue
        seen.add(key)
        if candidate.is_file():
            return candidate

    raise GalleryMediaError("photo file not found")


def _thumb_cache_path(source: Path, max_edge: int) -> Path:
    digest = hashlib.sha256(f"{source.resolve()}:{max_edge}".encode()).hexdigest()[:20]
    cache_dir = config.DATA_DIR / "offer_thumbs"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir / f"{digest}_{max_edge}.jpg"


def render_jpeg(path: Path, *, max_edge: int) -> bytes:
    cache = _thumb_cache_path(path, max_edge)
    if cache.exists():
        try:
            if cache.stat().st_mtime >= path.stat().st_mtime:
                return cache.read_bytes()
        except OSError:
            pass

    with Image.open(path) as im:
        im = ImageOps.exif_transpose(im)
        if im.mode not in ("RGB", "L"):
            im = im.convert("RGB")
        w, h = im.size
        scale = min(1.0, max_edge / max(w, h))
        if scale < 1.0:
            im = im.resize((int(w * scale), int(h * scale)), Image.Resampling.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=85, optimize=True)
        data = buf.getvalue()

    try:
        cache.write_bytes(data)
    except OSError:
        log.warning("could not cache offer thumb %s", cache)
    return data


def enrich_bundles_for_offer(
    *,
    store_slug: str,
    token: str,
    bundles: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for bundle in bundles:
        enriched = dict(bundle)
        items_out = []
        for item in bundle.get("items") or []:
            row = dict(item)
            photo = dict(row.get("photo") or {})
            if photo.get("filename"):
                photo["thumb_url"] = offer_photo_path(
                    store_slug, token, str(photo["filename"]), size="thumb"
                )
                photo["full_url"] = offer_photo_path(
                    store_slug, token, str(photo["filename"]), size="full"
                )
            row["photo"] = photo
            items_out.append(row)
        enriched["items"] = items_out
        slot_urls = []
        for slot in bundle.get("photo_slots") or []:
            if slot:
                slot_urls.append(
                    offer_photo_path(store_slug, token, str(slot), size="thumb")
                )
        enriched["photo_slot_urls"] = slot_urls
        hero = items_out[0]["photo"]["thumb_url"] if items_out and items_out[0].get("photo", {}).get("thumb_url") else (
            slot_urls[0] if slot_urls else None
        )
        enriched["hero_thumb_url"] = hero
        out.append(enriched)
    return out