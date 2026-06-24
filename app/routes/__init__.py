"""Register Plutus HTTP route modules."""
from __future__ import annotations

from fastapi import FastAPI


def register_routes(app: FastAPI) -> None:
    from .health import router as health_router
    app.include_router(health_router)
    from .api import router as api_router
    app.include_router(api_router)
    from .webhooks import router as webhooks_router
    app.include_router(webhooks_router)
    from .storefront import router as storefront_router
    app.include_router(storefront_router)
    from .homelab_ui import router as homelab_ui_router
    app.include_router(homelab_ui_router)
    from .saas_public import router as saas_public_router
    app.include_router(saas_public_router)
    from .saas_app import router as saas_app_router
    app.include_router(saas_app_router)
    from .saas_mutations import router as saas_mutations_router
    app.include_router(saas_mutations_router)
    from .saas_billing import router as saas_billing_router
    app.include_router(saas_billing_router)
