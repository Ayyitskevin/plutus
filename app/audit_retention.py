"""Purge audit events older than PLUTUS_AUDIT_LOG_RETENTION_DAYS."""
from __future__ import annotations

import logging

from . import config, db

log = logging.getLogger("plutus.audit_retention")


def purge_stale_audit_events() -> int:
    if not config.AUDIT_LOG_ENABLED:
        return 0
    days = config.AUDIT_LOG_RETENTION_DAYS
    if days <= 0:
        return 0
    count = db.purge_audit_events(older_than_days=days)
    if count:
        log.info("purged %s audit event(s) older than %s days", count, days)
    return count