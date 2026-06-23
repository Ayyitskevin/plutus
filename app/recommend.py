"""Mock recommendation engine — maps gallery photos to print/album bundles."""
from __future__ import annotations

from typing import Any

from .catalog import PRODUCTS, Product

Photo = dict[str, Any]


def _score(photo: Photo) -> float:
    keeper = float(photo.get("keeper_score") or 0.72)
    hero = float(photo.get("hero_potential") or 0.5)
    orient_bonus = 0.05 if photo.get("orientation") == "landscape" else 0.0
    return min(1.0, keeper * 0.7 + hero * 0.25 + orient_bonus)


def _pick_top(photos: list[Photo], n: int) -> list[Photo]:
    ranked = sorted(photos, key=_score, reverse=True)
    return ranked[:n]


def _line_item(product: Product, photo: Photo, *, qty: int = 1) -> dict[str, Any]:
    return {
        "sku": product.sku,
        "label": product.label,
        "size": product.size,
        "unit_cents": product.unit_cents,
        "qty": qty,
        "line_cents": product.unit_cents * qty,
        "photo": {
            "filename": photo["filename"],
            "path": photo.get("path"),
            "keeper_score": photo.get("keeper_score"),
            "hero_potential": photo.get("hero_potential"),
        },
        "rationale": _rationale(product, photo),
    }


def _rationale(product: Product, photo: Photo) -> str:
    bits = []
    if photo.get("keeper_score"):
        bits.append(f"keeper {float(photo['keeper_score']):.0%}")
    if photo.get("hero_potential"):
        bits.append(f"hero {float(photo['hero_potential']):.0%}")
    if photo.get("shot_type"):
        bits.append(str(photo["shot_type"]).replace("_", " "))
    detail = ", ".join(bits) if bits else "strong composition for wall display"
    return f"{product.label} {product.size} — {detail}"


def recommend_bundles(photos: list[Photo]) -> dict[str, Any]:
    """Return client-ready upsell bundles for a gallery."""
    if not photos:
        return {"bundles": [], "estimated_total_cents": 0, "photo_count": 0}

    top = _pick_top(photos, min(12, len(photos)))
    hero = top[0]
    runners = top[1:4]
    detail_shots = [
        p for p in photos
        if (p.get("shot_type") or "").lower() in {"detail", "macro", "ingredient"}
        or (p.get("orientation") == "square" and _score(p) >= 0.65)
    ][:3]

    bundles: list[dict[str, Any]] = []

    canvas = next(p for p in PRODUCTS if p.sku == "canvas-16x20")
    bundles.append({
        "id": "wall-hero",
        "title": "Statement wall piece",
        "pitch": "Lead with your strongest hero — ideal for dining room or lobby.",
        "items": [_line_item(canvas, hero)],
    })

    print_large = next(p for p in PRODUCTS if p.sku == "print-16x20")
    bundles.append({
        "id": "editor-picks",
        "title": "Editor's pick trio",
        "pitch": "Three large fine-art prints from your highest-scoring frames.",
        "items": [_line_item(print_large, p) for p in top[:3]],
    })

    gift = next(p for p in PRODUCTS if p.sku == "gift-trio-8x10")
    gift_photos = detail_shots or runners[:3] or top[:3]
    bundles.append({
        "id": "gift-trio",
        "title": "Giftable tabletop set",
        "pitch": "Smaller prints clients love for gifts and secondary spaces.",
        "items": [_line_item(gift, gift_photos[0], qty=1)] if gift_photos else [],
        "photo_slots": [p["filename"] for p in gift_photos[:3]],
    })

    keepers = [p for p in photos if _score(p) >= 0.68]
    if len(keepers) >= 12:
        album = next(
            p for p in PRODUCTS
            if p.sku == ("album-30" if len(keepers) >= 24 else "album-20")
        )
        bundles.append({
            "id": "signature-album",
            "title": "Signature layflat album",
            "pitch": f"{len(keepers)} keepers — strong candidate for a premium album upsell.",
            "items": [_line_item(album, keepers[0])],
            "photo_slots": [p["filename"] for p in _pick_top(keepers, 30)],
        })

    total = sum(
        item["line_cents"]
        for bundle in bundles
        for item in bundle.get("items") or []
    )

    return {
        "engine": "mock",
        "photo_count": len(photos),
        "bundles": bundles,
        "estimated_total_cents": total,
        "top_photos": [
            {"filename": p["filename"], "score": round(_score(p), 3)}
            for p in top[:6]
        ],
    }