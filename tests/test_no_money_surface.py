"""PR4a — studio mode exposes no client checkout / order / fulfillment surface.

Locks in the strip: the reachable homelab storefront/order/lab routes are gone,
while the legitimate studio routes (bundle editor, pitch, review) remain. Asserts
against the app's registered route table so it can't be fooled by a handler that
returns 404 for a missing record.
"""
from __future__ import annotations

from app.main import app


def _registered_paths() -> set[str]:
    """All registered paths, descending into this app's _IncludedRouter wrappers."""
    out: set[str] = set()
    for route in app.routes:
        path = getattr(route, "path", None)
        if path:
            out.add(path)
        original = getattr(route, "original_router", None)
        if original is not None:
            for sub in getattr(original, "routes", []):
                sub_path = getattr(sub, "path", None)
                if sub_path:
                    out.add(sub_path)
    return out


_PATHS = _registered_paths()

REMOVED_MONEY_PATHS = [
    "/ui/homelab/share-link",
    "/ui/homelab/orders/{order_id}",
    "/ui/homelab/orders/{order_id}/photo/{filename}",
    "/ui/homelab/orders/{order_id}/poll-lab",
    "/ui/homelab/orders/{order_id}/resend-confirmation",
]

KEPT_STUDIO_PATHS = [
    "/recommend/mise-gallery",
    "/ui/homelab/run-edit",
    "/runs/{run_id}/edit",
    "/runs/{run_id}/pitch.txt",
    "/healthz",
]


def test_money_routes_are_not_registered():
    leaked = [p for p in REMOVED_MONEY_PATHS if p in _PATHS]
    assert not leaked, f"client checkout/order/fulfillment routes must be gone: {leaked}"


def test_studio_routes_still_registered():
    missing = [p for p in KEPT_STUDIO_PATHS if p not in _PATHS]
    assert not missing, f"studio routes must remain: {missing}"
