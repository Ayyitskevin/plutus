"""Plutus — print & album upsell for Mise gallery admin."""
from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from . import config, db
from .routes import register_routes

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("plutus")

_ROOT = Path(__file__).resolve().parent.parent


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    db.migrate()
    yield


app = FastAPI(
    title="plutus",
    version="1.0.0",
    description=(
        "Print & album upsell recommendations for Mise galleries. "
        "Authenticate API calls with `Authorization: Bearer <PLUTUS_API_TOKEN>`."
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