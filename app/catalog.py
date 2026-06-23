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


def _override_map(tenant_id: str | None) -> dict[str, dict]:
    if not tenant_id:
        return {}
    from . import db

    return {row["sku"]: row for row in db.list_product_overrides(tenant_id)}


def unit_cents_for(sku: str, tenant_id: str | None = None) -> int:
    product = get_product(sku)
    if not product:
        return 0
    override = _override_map(tenant_id).get(sku)
    if override and override.get("active", True) and override.get("unit_cents") is not None:
        return int(override["unit_cents"])
    return product.unit_cents


def label_for(sku: str, tenant_id: str | None = None) -> str:
    product = get_product(sku)
    if not product:
        return sku
    override = _override_map(tenant_id).get(sku)
    if override and override.get("label"):
        return str(override["label"])
    return product.label


def is_active(sku: str, tenant_id: str | None = None) -> bool:
    override = _override_map(tenant_id).get(sku)
    if override is not None:
        return bool(override.get("active", True))
    return get_product(sku) is not None


def list_catalog(tenant_id: str | None = None) -> list[dict]:
    """Merge base SKUs with tenant overrides for pricing UI."""
    overrides = _override_map(tenant_id)
    rows = []
    for product in PRODUCTS:
        override = overrides.get(product.sku)
        active = override.get("active", True) if override else True
        unit_cents = (
            int(override["unit_cents"])
            if override and override.get("unit_cents") is not None
            else product.unit_cents
        )
        label = override.get("label") if override and override.get("label") else product.label
        rows.append(
            {
                "sku": product.sku,
                "label": label,
                "base_label": product.label,
                "category": product.category,
                "size": product.size,
                "unit_cents": unit_cents,
                "base_cents": product.unit_cents,
                "active": active,
                "has_override": override is not None,
                "notes": product.notes,
            }
        )
    return rows