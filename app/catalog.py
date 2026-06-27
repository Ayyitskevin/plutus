"""Print & album product catalog with optional tenant price overrides."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Product:
    sku: str
    label: str
    category: str
    size: str
    unit_cents: int
    min_dpi: int = 240
    notes: str = ""


PRODUCTS: tuple[Product, ...] = (
    Product("print-8x10", "Fine Art Print", "print", "8×10″", 4500),
    Product("print-11x14", "Fine Art Print", "print", "11×14″", 7500),
    Product("print-16x20", "Fine Art Print", "print", "16×20″", 12000),
    Product("canvas-16x20", "Canvas Wrap", "canvas", "16×20″", 18500),
    Product("canvas-24x36", "Canvas Wrap", "canvas", "24×36″", 32000),
    Product("metal-11x14", "Metal Print", "metal", "11×14″", 14500),
    Product("album-20", "Layflat Album", "album", "20 spreads", 28500,
            notes="Up to 40 photos across spreads"),
    Product("album-30", "Layflat Album", "album", "30 spreads", 38500,
            notes="Up to 60 photos across spreads"),
    Product("gift-trio-8x10", "Gift Trio", "bundle", "3× 8×10″", 11000,
            notes="Three matching tabletop prints"),
)

_BY_SKU = {p.sku: p for p in PRODUCTS}


def get_product(sku: str) -> Product | None:
    return _BY_SKU.get(sku)


def is_album_sku(sku: str) -> bool:
    product = get_product(sku)
    return product is not None and product.category == "album"


def bundles_include_album(bundles: list) -> bool:
    for bundle in bundles:
        for item in bundle.get("items") or []:
            if is_album_sku(str(item.get("sku") or "")):
                return True
    return False


def unit_cents_for(sku: str) -> int:
    product = get_product(sku)
    return product.unit_cents if product else 0


def label_for(sku: str) -> str:
    product = get_product(sku)
    return product.label if product else sku