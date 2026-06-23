"""Single-tenant homelab storefront — client checkout without full SaaS."""
from __future__ import annotations

import logging

from . import config, db, tenants

log = logging.getLogger("plutus.homelab")


def store_enabled() -> bool:
    return not config.SAAS_MODE and config.HOMELAB_STORE_ENABLED


def tenant_id() -> str:
    return config.HOMELAB_TENANT_ID


def ensure_bootstrap() -> dict:
    """Create homelab studio tenant and attach orphan runs/galleries."""
    db.migrate()
    row = db.get_tenant(tenant_id())
    if not row:
        tenants.create_tenant(
            tenant_id(),
            name=config.HOMELAB_STUDIO_NAME,
            store_slug=config.HOMELAB_STORE_SLUG,
        )
        log.info(
            "homelab tenant %s created (store /store/%s)",
            tenant_id(),
            config.HOMELAB_STORE_SLUG,
        )
    tid = tenant_id()
    with db.connection() as con:
        con.execute(
            "UPDATE galleries SET tenant_id=? WHERE tenant_id IS NULL",
            (tid,),
        )
        con.execute(
            "UPDATE recommendation_runs SET tenant_id=? WHERE tenant_id IS NULL",
            (tid,),
        )
    return db.get_tenant(tid) or {}