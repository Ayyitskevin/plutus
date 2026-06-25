"""Register Plutus HTTP route modules (studio / Mise feature mode)."""
from __future__ import annotations

from fastapi import FastAPI


def register_routes(app: FastAPI) -> None:
    from .health import router as health_router

    app.include_router(health_router)
    from .api import router as api_router

    app.include_router(api_router)
    from .homelab_ui import router as homelab_ui_router

    app.include_router(homelab_ui_router)