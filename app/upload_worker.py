"""Background worker — async upload batch analyze (Argus + recommend)."""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from . import config, db, service

log = logging.getLogger("plutus.upload_worker")


def requeue_stale_batches() -> int:
    """Re-queue batches stuck in analyzing (worker crash / timeout)."""
    stale_before = (
        datetime.now(UTC) - timedelta(minutes=config.UPLOAD_ANALYZE_STALE_MINUTES)
    ).isoformat()
    count = db.requeue_stale_analyzing_batches(stale_before_iso=stale_before)
    if count:
        log.warning("requeued %s stale analyzing upload batch(es)", count)
    return count


def process_pending_batches(*, limit: int = 1) -> int:
    """Process queued upload batches; returns count completed."""
    requeue_stale_batches()
    processed = 0
    for _ in range(limit):
        batch = db.claim_upload_batch_for_processing()
        if not batch:
            break
        batch_id = batch["id"]
        tenant_id = batch["tenant_id"]
        argus_run_id = batch.get("argus_run_id")
        try:
            service.process_upload_batch_analyze(
                batch_id,
                tenant_id=tenant_id,
                name=batch.get("name"),
                argus_run_id=int(argus_run_id) if argus_run_id else None,
            )
            processed += 1
            log.info("upload batch %s analyzed for tenant %s", batch_id, tenant_id)
        except Exception as exc:
            log.exception("upload batch %s failed", batch_id)
            db.update_upload_batch(
                batch_id,
                status="failed",
                analyze_error=str(exc)[:500],
                analyze_started_at=None,
            )
    return processed