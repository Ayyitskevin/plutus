from __future__ import annotations

import logging

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse

from .. import (
    billing,
)
from ..auth_context import AuthContext
from .deps import (
    templates,
    tenant_ui_redirect,
    ui_context,
)

log = logging.getLogger("plutus")
router = APIRouter()

@router.get("/ui/saas/billing", response_class=HTMLResponse)
def ui_saas_billing(
    request: Request,
    success: str | None = Query(None),
    cancelled: str | None = Query(None),
):
    ctx = tenant_ui_redirect(request)
    if not isinstance(ctx, AuthContext):
        return ctx
    from ..metering import usage_snapshot

    usage = usage_snapshot(ctx.tenant_id)
    return templates.TemplateResponse(
        request,
        "saas_billing.html",
        ui_context(request, 
            title="Billing",
            tenant=ctx.tenant,
            usage=usage,
            subscription=billing.tenant_subscription_view(ctx.tenant),
            billing_success=bool(success),
            billing_cancelled=bool(cancelled),
            billing_info=billing.billing_status(),
        ),
    )


