from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse

from .. import config, health, metrics, mise_client, offer_schema, recommend

log = logging.getLogger("plutus")
router = APIRouter()


@router.get("/healthz")
def healthz() -> dict:
    report = health.build_health_report()
    report.update({
        "service": "plutus",
        "engine": "mock",
        # Provenance/version surface so Mise can detect contract or engine drift.
        "model": recommend.MODEL_VERSION,
        "offer_schema_version": offer_schema.OFFER_SCHEMA_VERSION,
        "mise_configured": mise_client.is_enabled(),
        "auth_enabled": bool(config.API_TOKEN),
    })
    return report


@router.get("/metrics")
def metrics_endpoint() -> PlainTextResponse:
    if not config.PROMETHEUS_ENABLED:
        raise HTTPException(status_code=404, detail="prometheus disabled")
    return PlainTextResponse(metrics.prometheus_text(), media_type="text/plain; version=0.0.4")