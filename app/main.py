"""Plutus FastAPI app — print & album upsell recommendations."""
from __future__ import annotations

import logging
import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from . import audit_retention, config, db, homelab, lab, rate_limit, saas, upload_worker
from .routes import register_routes

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("plutus")

_ROOT = Path(__file__).resolve().parent.parent


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    saas.validate_saas_startup()
    db.migrate()
    if homelab.store_enabled():
        homelab.ensure_bootstrap()
    stop_poll = threading.Event()

    def _lab_poll_loop() -> None:
        while not stop_poll.wait(60):
            try:
                lab.poll_pending_orders()
            except Exception:
                log.exception("lab poll loop error")

    poll_thread = None
    if lab.lab_enabled() and config.LAB_ADAPTER in {"mock", "whcc"}:
        if config.SAAS_MODE or homelab.store_enabled():
            poll_thread = threading.Thread(target=_lab_poll_loop, daemon=True, name="lab-poll")
            poll_thread.start()

    def _upload_worker_loop() -> None:
        while not stop_poll.wait(config.UPLOAD_WORKER_INTERVAL):
            try:
                upload_worker.process_pending_batches()
            except Exception:
                log.exception("upload worker loop error")

    upload_thread = None
    if config.SAAS_MODE and config.UPLOAD_ASYNC_ANALYZE:
        upload_thread = threading.Thread(
            target=_upload_worker_loop, daemon=True, name="upload-worker"
        )
        upload_thread.start()

    def _audit_purge_loop() -> None:
        while not stop_poll.wait(3600):
            try:
                audit_retention.purge_stale_audit_events()
            except Exception:
                log.exception("audit purge loop error")

    audit_thread = None
    if config.SAAS_MODE and config.AUDIT_LOG_ENABLED and config.AUDIT_LOG_RETENTION_DAYS > 0:
        audit_thread = threading.Thread(target=_audit_purge_loop, daemon=True, name="audit-purge")
        audit_thread.start()
    try:
        yield
    finally:
        stop_poll.set()
        grace = config.SHUTDOWN_GRACE_SECONDS
        if poll_thread:
            poll_thread.join(timeout=grace)
        if upload_thread:
            upload_thread.join(timeout=grace)
        if audit_thread:
            audit_thread.join(timeout=grace)


app = FastAPI(
    title="plutus",
    version="1.0.0",
    description=(
        "Print & album upsell for photo galleries. "
        "SaaS tenants authenticate with `Authorization: Bearer plutus_tk_<tenant>_<token>`."
    ),
    lifespan=lifespan,
)

if config.CORS_ORIGINS:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=config.CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
app.middleware("http")(rate_limit.rate_limit_middleware)
app.middleware("http")(saas.saas_auth_middleware)
app.mount("/static", StaticFiles(directory=str(_ROOT / "static")), name="static")



register_routes(app)


def main() -> None:
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=config.HOST,
        port=config.PORT,
        reload=False,
    )


if __name__ == "__main__":
    main()
