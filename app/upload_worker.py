"""Background worker — async upload batch analyze (Argus + recommend)."""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from . import config, db, redis_client, service

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
    lock_name = "upload-worker:claim"
    for _ in range(limit):
        if config.REDIS_URL and db.backend_name() == "sqlite":
            lock_ttl = max(30, config.UPLOAD_WORKER_INTERVAL * 3)
            if not redis_client.acquire_lock(lock_name, ttl_seconds=lock_ttl):
                break
        batch = db.claim_upload_batch_for_processing()
        if not batch:
            if config.REDIS_URL and db.backend_name() == "sqlite":
                redis_client.release_lock(lock_name)
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
        finally:
            if config.REDIS_URL and db.backend_name() == "sqlite":
                redis_client.release_lock(lock_name)
    return processed