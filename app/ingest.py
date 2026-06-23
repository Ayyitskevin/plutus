"""Gallery ingest — local folder or Argus manifest."""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps

from . import config

Photo = dict[str, Any]


def _dims(path: Path) -> tuple[int, int]:
    with Image.open(path) as im:
        im = ImageOps.exif_transpose(im)
        return im.size


def photos_from_folder(folder: Path, *, limit: int | None = None) -> list[Photo]:
    folder = folder.expanduser().resolve()
    if not folder.is_dir():
        raise FileNotFoundError(f"not a directory: {folder}")

    paths = sorted(
        p for p in folder.iterdir()
        if p.is_file() and p.suffix.lower() in config.PHOTO_EXTS
    )
    if limit:
        paths = paths[:limit]

    photos: list[Photo] = []
    for path in paths:
        w, h = _dims(path)
        photos.append({
            "path": str(path),
            "filename": path.name,
            "width": w,
            "height": h,
            "orientation": "portrait" if h > w else "landscape" if w > h else "square",
            "keeper_score": None,
            "hero_potential": None,
            "shot_type": None,
            "keywords": [],
        })
    return photos


def _merge_argus_photo(base: Photo, argus_row: dict[str, Any]) -> Photo:
    culling = argus_row.get("culling") or {}
    merged = dict(base)
    merged["keeper_score"] = culling.get("keeper_score")
    merged["hero_potential"] = culling.get("hero_potential")
    merged["shot_type"] = argus_row.get("shot_type")
    merged["keywords"] = list(argus_row.get("keywords") or [])
    return merged


def enrich_from_argus_run(photos: list[Photo], run_id: int) -> list[Photo]:
    """Overlay keeper/hero signals from an Argus export manifest."""
    if not (config.ARGUS_URL and config.ARGUS_TOKEN):
        return photos

    url = f"{config.ARGUS_URL}/runs/{run_id}/export"
    req = urllib.request.Request(
        url, headers={"Authorization": f"Bearer {config.ARGUS_TOKEN}"}
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            payload = json.loads(resp.read().decode())
    except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError, TimeoutError):
        return photos

    by_name: dict[str, dict[str, Any]] = {}
    for row in payload.get("photos") or []:
        path = row.get("image_path") or row.get("path") or ""
        by_name[Path(path).name] = row

    out: list[Photo] = []
    for photo in photos:
        row = by_name.get(photo["filename"])
        out.append(_merge_argus_photo(photo, row) if row else photo)
    return out