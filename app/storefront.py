"""Client-facing storefront helpers."""
from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from . import config, db, orders


class StorefrontError(Exception):
    """Storefront access or checkout failure."""


def public_offer_url(store_slug: str, token: str) -> str:
    base = config.SAAS_PUBLIC_URL.rstrip("/")
    return f"{base}/store/{store_slug}/offer/{token}"


def create_share_link(
    *,
    tenant_id: str,
    run_id: int,
    label: str | None = None,
    expires_days: int | None = 30,
) -> dict:
    run = db.get_run(run_id, tenant_id=tenant_id)
    if not run:
        raise StorefrontError("run not found")
    tenant = db.get_tenant(tenant_id)
    if not tenant:
        raise StorefrontError("tenant not found")
    token = secrets.token_urlsafe(24)
    expires_at = None
    if expires_days and expires_days > 0:
        expires_at = (datetime.now(UTC) + timedelta(days=expires_days)).isoformat()
    row = db.create_storefront_token(
        token=token,
        tenant_id=tenant_id,
        run_id=run_id,
        label=label,
        expires_at=expires_at,
    )
    slug = tenant.get("store_slug") or tenant_id
    path = f"/store/{slug}/offer/{token}"
    return {
        "token": token,
        "url": path,
        "public_url": public_offer_url(slug, token),
        "store_slug": slug,
        **row,
    }


def resolve_offer(store_slug: str, token: str) -> dict[str, Any]:
    tenant = db.get_tenant_by_slug(store_slug)
    if not tenant:
        raise StorefrontError("store not found")
    link = db.get_storefront_token(token)
    if not link or link["tenant_id"] != tenant["id"]:
        raise StorefrontError("offer link not found")
    if link.get("expires_at"):
        try:
            exp_dt = datetime.fromisoformat(str(link["expires_at"]).replace("Z", "+00:00"))
            if exp_dt.tzinfo is None:
                exp_dt = exp_dt.replace(tzinfo=UTC)
            if datetime.now(UTC) > exp_dt:
                raise StorefrontError("offer link expired")
        except ValueError as exc:
            raise StorefrontError("offer link expired") from exc
    run = db.get_run(int(link["run_id"]), tenant_id=tenant["id"])
    if not run:
        raise StorefrontError("offer no longer available")
    gallery_name = db.get_gallery_name(run["gallery_id"]) or f"Gallery {run['gallery_id']}"
    bundles = run["payload"].get("bundles") or []
    priced_bundles = []
    for idx, bundle in enumerate(bundles):
        total = orders.bundle_total_cents(bundle, tenant["id"])
        priced_bundles.append({**bundle, "index": idx, "total_cents": total})
    return {
        "tenant": tenant,
        "run": run,
        "gallery_name": gallery_name,
        "bundles": priced_bundles,
        "token": token,
        "link": link,
    }