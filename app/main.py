"""Plutus FastAPI app — print & album upsell recommendations."""
from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import auth, config, db, pitch, service

_ROOT = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=str(_ROOT / "templates"))


def _fmt_cents(cents: int) -> str:
    return f"${cents / 100:,.2f}"


templates.env.filters["money"] = _fmt_cents


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    db.migrate()
    yield


app = FastAPI(title="plutus", version="0.1.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(_ROOT / "static")), name="static")


@app.get("/healthz")
def healthz() -> dict:
    from . import mise_client

    return {
        "status": "ok",
        "service": "plutus",
        "engine": "mock",
        "mise_configured": mise_client.is_enabled(),
        "auth_enabled": bool(config.API_TOKEN),
    }


@app.post("/recommend/mise-gallery", dependencies=[Depends(auth.require_token)])
def recommend_mise_gallery_api(
    mise_gallery_id: int = Form(...),
    limit: int | None = Form(None),
    argus_run_id: int | None = Form(None),
) -> JSONResponse:
    try:
        result = service.analyze_mise_gallery(
            mise_gallery_id, limit=limit, argus_run_id=argus_run_id
        )
    except service.RecommendError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return JSONResponse(result)


@app.post("/analyze-folder")
def analyze_folder_api(
    folder: str = Form(...),
    name: str | None = Form(None),
    argus_run_id: int | None = Form(None),
    limit: int | None = Form(None),
) -> JSONResponse:
    path = Path(folder).expanduser()
    try:
        result = service.analyze_folder(
            path, name=name, argus_run_id=argus_run_id, limit=limit
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return JSONResponse(result)


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    runs = db.list_runs(limit=10)
    return templates.TemplateResponse(
        request, "index.html", {"runs": runs, "title": "upsell"}
    )


@app.post("/analyze", response_class=HTMLResponse)
def analyze_form(
    request: Request,
    folder: str = Form(...),
    name: str | None = Form(None),
    argus_run_id: int | None = Form(None),
    limit: int | None = Form(None),
):
    path = Path(folder).expanduser()
    try:
        result = service.analyze_folder(
            path, name=name, argus_run_id=argus_run_id, limit=limit
        )
    except FileNotFoundError:
        return templates.TemplateResponse(
            request,
            "index.html",
            {"error": f"Folder not found: {folder}", "runs": db.list_runs(limit=10)},
            status_code=400,
        )
    return RedirectResponse(f"/runs/{result['run_id']}", status_code=303)


@app.get("/runs/{run_id}", response_class=HTMLResponse)
def view_run(request: Request, run_id: int):
    row = db.get_run(run_id)
    if not row:
        return HTMLResponse("Run not found", status_code=404)
    payload = row["payload"]
    gallery_name = db.get_gallery_name(row["gallery_id"]) or f"Run {run_id}"
    pitch_text = pitch.render_pitch(
        gallery_name=gallery_name,
        bundles=payload.get("bundles") or [],
        estimated_total_cents=int(payload.get("estimated_total_cents") or 0),
        photo_count=int(payload.get("photo_count") or 0),
    )
    return templates.TemplateResponse(
        request,
        "run.html",
        {
            "run": row,
            "bundles": payload.get("bundles") or [],
            "top_photos": payload.get("top_photos") or [],
            "photo_count": payload.get("photo_count", 0),
            "estimated_total_cents": payload.get("estimated_total_cents", 0),
            "pitch_text": pitch_text,
            "title": f"run {run_id}",
        },
    )


@app.get("/runs/{run_id}/json", response_class=JSONResponse)
def run_json(run_id: int):
    row = db.get_run(run_id)
    if not row:
        raise HTTPException(status_code=404, detail="run not found")
    return row


@app.get("/runs/{run_id}/pitch.txt", response_class=PlainTextResponse)
def run_pitch(run_id: int):
    row = db.get_run(run_id)
    if not row:
        raise HTTPException(status_code=404, detail="run not found")
    payload = row["payload"]
    gallery_name = db.get_gallery_name(row["gallery_id"]) or f"Run {run_id}"
    return pitch.render_pitch(
        gallery_name=gallery_name,
        bundles=payload.get("bundles") or [],
        estimated_total_cents=int(payload.get("estimated_total_cents") or 0),
        photo_count=int(payload.get("photo_count") or 0),
    )


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