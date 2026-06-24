from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse

from .. import (
    billing,
    config,
    health,
    lab,
    metrics,
    signup,
)

log = logging.getLogger("plutus")
router = APIRouter()

@router.get("/healthz")
def healthz() -> dict:
    from .. import mise_client

    report = health.build_health_report()
    report.update({
        "service": "plutus",
        "engine": "mock",
        "mise_configured": mise_client.is_enabled(),
        "auth_enabled": bool(config.API_TOKEN),
    })
    return report



@router.get("/saas/status")
def saas_status() -> dict:
    return {
        "saas_mode": config.SAAS_MODE,
        "billing": billing.billing_status(),
        "signup_enabled": signup.signup_enabled(),
        "lab": lab.fulfillment_status(),
    }



@router.get("/saas/billing/status")
def saas_billing_status() -> dict:
    return billing.billing_status()



@router.get("/metrics")
def metrics_endpoint() -> PlainTextResponse:
    if not config.PROMETHEUS_ENABLED:
        raise HTTPException(status_code=404, detail="prometheus disabled")
    return PlainTextResponse(metrics.prometheus_text(), media_type="text/plain; version=0.0.4")




