"""Inbound Mise publish hook — auto-recommend for homelab or SaaS tenant."""
from __future__ import annotations

import secrets

from fastapi import HTTPException, Request

from . import config, homelab, service
from .metering import MeteringError


def verify_hook_token(request: Request) -> None:
    expected = config.MISE_HOOK_TOKEN
    if not expected:
        raise HTTPException(status_code=503, detail="Mise hook not configured")
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    provided = header.removeprefix("Bearer ").strip()
    if not secrets.compare_digest(provided, expected):
        raise HTTPException(status_code=401, detail="invalid hook token")


def resolve_hook_tenant_id(explicit: str | None) -> str | None:
    if explicit and explicit.strip():
        return explicit.strip()
    if config.SAAS_MODE:
        return config.MISE_HOOK_TENANT_ID
    if homelab.store_enabled():
        homelab.ensure_bootstrap()
        return homelab.tenant_id()
    return None


def recommend_published_gallery(
    *,
    mise_gallery_id: int,
    tenant_id: str | None,
    argus_run_id: int | None = None,
    limit: int | None = None,
) -> dict:
    scope = resolve_hook_tenant_id(tenant_id)
    if config.SAAS_MODE and not scope:
        raise HTTPException(
            status_code=400,
            detail="tenant_id required (or set PLUTUS_MISE_HOOK_TENANT_ID)",
        )
    try:
        return service.analyze_mise_gallery(
            mise_gallery_id,
            limit=limit,
            argus_run_id=argus_run_id,
            tenant_id=scope,
        )
    except MeteringError as exc:
        raise HTTPException(status_code=402, detail=str(exc)) from exc
    except service.RecommendError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc