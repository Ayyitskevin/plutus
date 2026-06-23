"""Background worker — async upload batch analyze (Argus + recommend)."""
from __future__ import annotations

import logging

from . import db, service

log = logging.getLogger("plutus.upload_worker")


def process_pending_batches(*, limit: int = 1) -> int:
    """Process queued upload batches; returns count completed."""
    processed = 0
    for batch in db.list_upload_batches_by_status("queued", limit=limit):
        batch_id = batch["id"]
        tenant_id = batch["tenant_id"]
        db.update_upload_batch(batch_id, status="analyzing", analyze_error=None)
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
            db.update_upload_batch(batch_id, status="failed", analyze_error=str(exc)[:500])
    return processed