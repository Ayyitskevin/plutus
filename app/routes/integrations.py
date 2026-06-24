"""Cross-product integration endpoints (mnemosyne, Mise hooks, automation)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException
from fastapi.responses import JSONResponse

from ..auth import require_bearer
from ..auth_context import AuthContext
from ..storefront import StorefrontError, create_share_link, link_tenant_for_bearer

router = APIRouter()


@router.post("/integrations/offer", response_class=JSONResponse)
def api_integration_offer(
    run_id: int = Form(...),
    tenant_id: str | None = Form(None),
    label: str | None = Form(None),
    ctx: AuthContext = Depends(require_bearer),
) -> JSONResponse:
    """Mint a client offer link in one call — admin + tenant_id or tenant API key."""
    link_tenant = link_tenant_for_bearer(ctx, tenant_id)
    if not link_tenant:
        raise HTTPException(
            status_code=403,
            detail="tenant API key required (or admin with tenant_id)",
        )
    try:
        link = create_share_link(
            tenant_id=link_tenant,
            run_id=run_id,
            label=label.strip() if label else None,
        )
    except StorefrontError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return JSONResponse(link)