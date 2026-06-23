"""Tenant publish-and-sell — upload → recommend → client offer link."""
from __future__ import annotations

import logging
import time
from typing import Any

from . import config, db, upload_worker, uploads
from .storefront import StorefrontError, create_share_link

log = logging.getLogger("plutus.sell")


class SellError(Exception):
    """Publish flow failure safe for UI redirect."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def wait_batch_analyzed(
    batch_id: str,
    *,
    tenant_id: str,
    timeout: float | None = None,
) -> int:
    """Poll until upload batch has a recommendation run."""
    limit = float(timeout or config.SELL_ANALYZE_TIMEOUT)
    deadline = time.time() + limit
    while time.time() < deadline:
        batch = uploads.get_batch(batch_id, tenant_id=tenant_id)
        if not batch:
            raise SellError("upload batch not found")
        if batch.get("status") == "analyzed" and batch.get("run_id"):
            return int(batch["run_id"])
        if batch.get("status") == "failed":
            raise SellError(batch.get("analyze_error") or "gallery analyze failed")
        if batch.get("status") in {"queued", "analyzing"}:
            upload_worker.process_pending_batches()
        time.sleep(2)
    raise SellError(f"analyze did not finish within {int(limit)}s")


def resolve_run_id(
    tenant_id: str,
    *,
    run_id: int | None = None,
    batch_id: str | None = None,
) -> tuple[int, list[str]]:
    steps: list[str] = []
    if run_id:
        row = db.get_run(run_id, tenant_id=tenant_id)
        if not row:
            raise SellError(f"run {run_id} not found")
        steps.append(f"bundles run {run_id}")
        return run_id, steps

    if batch_id:
        rid = wait_batch_analyzed(batch_id, tenant_id=tenant_id)
        steps.append(f"analyzed batch → run {rid}")
        return rid, steps

    recent = db.list_runs(limit=1, tenant_id=tenant_id)
    if not recent:
        raise SellError("no bundles yet — upload a gallery first")
    rid = int(recent[0]["id"])
    steps.append(f"latest run {rid}")
    return rid, steps


def publish_offer(
    tenant_id: str,
    run_id: int,
    *,
    label: str | None = None,
) -> dict[str, Any]:
    tenant = db.get_tenant(tenant_id) or {}
    try:
        link = create_share_link(
            tenant_id=tenant_id,
            run_id=run_id,
            label=label or tenant.get("name"),
        )
    except StorefrontError as exc:
        raise SellError(str(exc)) from exc
    return {
        "run_id": run_id,
        "offer_url": link["public_url"],
        "offer_token": link["token"],
        "store_slug": link.get("store_slug"),
    }


def publish_and_sell(
    tenant_id: str,
    *,
    run_id: int | None = None,
    batch_id: str | None = None,
    label: str | None = None,
) -> dict[str, Any]:
    rid, steps = resolve_run_id(tenant_id, run_id=run_id, batch_id=batch_id)
    offer = publish_offer(tenant_id, rid, label=label)
    steps.append("client offer ready")
    return {**offer, "steps": steps}