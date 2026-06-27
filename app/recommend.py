"""Recommendation engine — vision-aware bundle builder."""
from __future__ import annotations

import time
from typing import Any

from . import catalog
from .catalog import PRODUCTS, Product

Photo = dict[str, Any]

# Provenance reported on every run (Mise persists this to its ai_runs ledger).
# The recommend step is a deterministic rules engine — no paid model call — so its
# own cost is 0.0. Vision signals come from Argus, which meters its own cost.
MODEL_VERSION = "plutus-rules-v1"

DETAIL_SHOT_TYPES = frozenset(
    {"detail", "macro", "ingredient", "close_up", "closeup", "texture", "flat_lay"}
)
HERO_SHOT_TYPES = frozenset(
    {"hero_plate", "hero", "establishing", "signature", "environment", "wide"}
)
PORTRAIT_SHOT_TYPES = frozenset(
    {"portrait", "headshot", "couple", "group", "family", "candid"}
)
FOOD_KEYWORDS = frozenset(
    {"food", "appetizer", "dish", "plating", "restaurant", "dessert", "cocktail", "menu"}
)
WEDDING_KEYWORDS = frozenset(
    {"wedding", "bride", "groom", "ceremony", "reception", "engagement", "elopement"}
)


def _normalized_shot(photo: Photo) -> str:
    return (photo.get("shot_type") or "").lower().replace("-", "_").strip()


def _keyword_set(photo: Photo) -> set[str]:
    return {(k or "").lower() for k in (photo.get("keywords") or []) if k}


def _has_vision_signals(photos: list[Photo]) -> bool:
    return any(
        p.get("keeper_score") is not None
        or p.get("hero_potential") is not None
        or p.get("shot_type")
        or p.get("keywords")
        for p in photos
    )


def _score(photo: Photo) -> float:
    keeper = float(photo.get("keeper_score") or 0.72)
    hero = float(photo.get("hero_potential") or 0.5)
    orient_bonus = 0.05 if photo.get("orientation") == "landscape" else 0.0
    shot = _normalized_shot(photo)
    shot_bonus = 0.0
    if shot in HERO_SHOT_TYPES:
        shot_bonus = 0.08
    elif shot in DETAIL_SHOT_TYPES:
        shot_bonus = 0.03
    return min(1.0, keeper * 0.7 + hero * 0.25 + orient_bonus + shot_bonus)


def _pick_top(photos: list[Photo], n: int) -> list[Photo]:
    return sorted(photos, key=_score, reverse=True)[:n]


def _is_detail(photo: Photo) -> bool:
    if _normalized_shot(photo) in DETAIL_SHOT_TYPES:
        return True
    kws = _keyword_set(photo)
    return bool(kws & {"detail", "macro", "ingredient", "texture", "closeup", "flat_lay"})


def _is_hero_candidate(photo: Photo) -> bool:
    if _normalized_shot(photo) in HERO_SHOT_TYPES:
        return True
    return float(photo.get("hero_potential") or 0) >= 0.75


def _gallery_theme(photos: list[Photo]) -> str:
    all_kws: set[str] = set()
    for photo in photos:
        all_kws |= _keyword_set(photo)
    if all_kws & WEDDING_KEYWORDS:
        return "wedding"
    if all_kws & FOOD_KEYWORDS:
        return "food"
    return "general"


def refresh_item_photo(item: dict[str, Any], photo: Photo) -> dict[str, Any]:
    """Re-bind a catalog line item to a different gallery photo."""
    product = catalog.get_product(str(item.get("sku") or ""))
    if not product:
        return item
    qty = int(item.get("qty") or item.get("quantity") or 1)
    refreshed = _line_item(product, photo, qty=qty)
    return refreshed if refreshed else item


def _line_item(
    product: Product,
    photo: Photo,
    *,
    qty: int = 1,
) -> dict[str, Any]:
    unit_cents = catalog.unit_cents_for(product.sku)
    label = catalog.label_for(product.sku)
    return {
        "sku": product.sku,
        "label": label,
        "size": product.size,
        "unit_cents": unit_cents,
        "qty": qty,
        "line_cents": unit_cents * qty,
        "photo": {
            "filename": photo["filename"],
            "path": photo.get("path"),
            "keeper_score": photo.get("keeper_score"),
            "hero_potential": photo.get("hero_potential"),
            "shot_type": photo.get("shot_type"),
            "keywords": list(photo.get("keywords") or [])[:5],
        },
        "rationale": _rationale(product, photo),
    }


def _rationale(product: Product, photo: Photo) -> str:
    bits = []
    if photo.get("keeper_score") is not None:
        bits.append(f"keeper {float(photo['keeper_score']):.0%}")
    if photo.get("hero_potential") is not None:
        bits.append(f"hero {float(photo['hero_potential']):.0%}")
    shot = _normalized_shot(photo)
    if shot:
        bits.append(shot.replace("_", " "))
    kws = [k for k in (photo.get("keywords") or [])[:3] if k]
    if kws:
        bits.append(", ".join(kws))
    detail = " · ".join(bits) if bits else "strong composition for wall display"
    return f"{product.label} {product.size} — {detail}"


def _pick_hero(photos: list[Photo]) -> Photo:
    heroes = [p for p in photos if _is_hero_candidate(p)]
    if heroes:
        return _pick_top(heroes, 1)[0]
    return _pick_top(photos, 1)[0]


def _pick_canvas_product(hero: Photo) -> Product:
    hero_score = float(hero.get("hero_potential") or 0)
    if hero_score >= 0.88 and hero.get("orientation") == "landscape":
        return next(p for p in PRODUCTS if p.sku == "canvas-24x36")
    return next(p for p in PRODUCTS if p.sku == "canvas-16x20")


def _wall_pitch(theme: str, hero: Photo) -> str:
    shot = _normalized_shot(hero).replace("_", " ")
    if theme == "food":
        return (
            f"Lead with this {shot or 'hero'} frame — "
            "perfect for restaurant or kitchen wall art."
        )
    if theme == "wedding":
        return (
            "Statement piece from your strongest keeper — "
            "ideal for the couple's home or parents' gift."
        )
    return "Lead with your strongest hero — ideal for dining room or lobby."


def _contract_line_item(item: dict[str, Any]) -> dict[str, Any]:
    """Project an internal catalog line into the Mise contract line_item shape.

    Superset of the contract minimum (`label`/`qty`/`unit_cents`): we also carry the
    catalog `sku` so Mise can map an accepted line to an invoice-line product.
    """
    qty = int(item.get("qty") or item.get("quantity") or 1)
    unit_cents = int(item.get("unit_cents") or 0)
    return {
        "sku": item.get("sku"),
        "label": item.get("label") or item.get("sku") or "",
        "qty": qty,
        "unit_cents": unit_cents,
    }


def _attach_contract_fields(bundle: dict[str, Any]) -> dict[str, Any]:
    """Add stable bundle `sku` + `line_items` so accepted offers link to invoices.

    `bundle["id"]` is a stable bundle-kind id (wall-hero, editor-picks, …) reused as
    the contract `sku`. Existing keys (`id`/`title`/`items`/`line_cents`) are kept as
    backward-compatible aliases for the current Mise consumer and the Plutus UI/pitch.
    """
    line_items = [_contract_line_item(it) for it in bundle.get("items") or []]
    bundle["sku"] = bundle.get("id")
    bundle["label"] = bundle.get("title")
    bundle["line_items"] = line_items
    bundle["estimated_cents"] = sum(li["qty"] * li["unit_cents"] for li in line_items)
    return bundle


def _provenance(start: float) -> dict[str, Any]:
    return {
        "model": MODEL_VERSION,
        "latency_ms": int((time.perf_counter() - start) * 1000),
        "cost_usd": 0.0,
    }


def recommend_bundles(photos: list[Photo]) -> dict[str, Any]:
    """Return client-ready upsell bundles for a gallery."""
    started = time.perf_counter()
    if not photos:
        return {
            "bundles": [],
            "estimated_total_cents": 0,
            "photo_count": 0,
            "engine": "mock",
            **_provenance(started),
        }

    theme = _gallery_theme(photos)
    vision = _has_vision_signals(photos)
    top = _pick_top(photos, min(12, len(photos)))
    hero = _pick_hero(photos)
    runners = top[1:4]

    detail_shots = [p for p in photos if _is_detail(p)]
    if not detail_shots and vision:
        detail_shots = [
            p for p in photos
            if (p.get("orientation") == "square" and _score(p) >= 0.65)
        ][:3]

    bundles: list[dict[str, Any]] = []

    canvas = _pick_canvas_product(hero)
    bundles.append({
        "id": "wall-hero",
        "title": "Statement wall piece",
        "pitch": _wall_pitch(theme, hero),
        "items": [_line_item(canvas, hero)],
    })

    print_large = next(p for p in PRODUCTS if p.sku == "print-16x20")
    editor_picks = top[:3]
    if theme == "wedding":
        portraits = [p for p in photos if _normalized_shot(p) in PORTRAIT_SHOT_TYPES]
        if len(portraits) >= 2:
            editor_picks = _pick_top(portraits, 3)
    bundles.append({
        "id": "editor-picks",
        "title": "Editor's pick trio",
        "pitch": "Three large fine-art prints from your highest-scoring frames.",
        "items": [_line_item(print_large, p) for p in editor_picks],
    })

    gift = next(p for p in PRODUCTS if p.sku == "gift-trio-8x10")
    gift_photos = detail_shots[:3] or runners[:3] or top[:3]
    gift_pitch = (
        "Detail and tabletop frames clients love for gifts and secondary spaces."
        if detail_shots
        else "Smaller prints clients love for gifts and secondary spaces."
    )
    bundles.append({
        "id": "gift-trio",
        "title": "Giftable tabletop set",
        "pitch": gift_pitch,
        "items": [_line_item(gift, gift_photos[0], qty=1)]
        if gift_photos
        else [],
        "photo_slots": [p["filename"] for p in gift_photos[:3]],
    })

    if theme == "food":
        metal = next(p for p in PRODUCTS if p.sku == "metal-11x14")
        metal_photo = detail_shots[0] if detail_shots else hero
        bundles.append({
            "id": "metal-accent",
            "title": "Metal kitchen accent",
            "pitch": "Vivid metal print for chef's table, bar, or menu wall.",
            "items": [_line_item(metal, metal_photo)],
        })

    keepers = [p for p in photos if _score(p) >= 0.68]
    if len(keepers) >= 12:
        album = next(
            p for p in PRODUCTS
            if p.sku == ("album-30" if len(keepers) >= 24 else "album-20")
        )
        album_pitch = {
            "wedding": f"{len(keepers)} keepers — signature album for the couple's story.",
            "food": f"{len(keepers)} strong frames — premium menu or brand lookbook album.",
        }.get(theme, f"{len(keepers)} keepers — strong candidate for a premium album upsell.")
        bundles.append({
            "id": "signature-album",
            "title": "Signature layflat album",
            "pitch": album_pitch,
            "items": [_line_item(album, _pick_top(keepers, 1)[0])],
            "photo_slots": [p["filename"] for p in _pick_top(keepers, 30)],
        })

    for bundle in bundles:
        bundle["items"] = [item for item in bundle.get("items") or [] if item]

    bundles = [b for b in bundles if b.get("items")]

    for bundle in bundles:
        _attach_contract_fields(bundle)

    total = sum(bundle["estimated_cents"] for bundle in bundles)

    return {
        "engine": "vision" if vision else "mock",
        "gallery_theme": theme,
        "photo_count": len(photos),
        "bundles": bundles,
        "estimated_total_cents": total,
        "top_photos": [
            {
                "filename": p["filename"],
                "score": round(_score(p), 3),
                "shot_type": p.get("shot_type"),
            }
            for p in top[:6]
        ],
        **_provenance(started),
    }