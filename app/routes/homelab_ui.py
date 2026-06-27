from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, Form, HTTPException, Query, Request
from fastapi.responses import (
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
    Response,
)

from .. import config, db, pitch, service
from ..bundle_editor import BundleEditError, photos_for_run, save_run_edits
from ..gallery_media import (
    FULL_MAX_EDGE,
    THUMB_MAX_EDGE,
    GalleryMediaError,
    enrich_bundles_for_run,
    enrich_top_photos_for_run,
    render_jpeg,
    resolve_photo_file,
)
from .deps import templates, ui_context

log = logging.getLogger("plutus")
router = APIRouter()


@router.get("/", response_class=HTMLResponse)
def home(request: Request):
    runs = db.list_runs(limit=10)
    return templates.TemplateResponse(request, "index.html", {"runs": runs, "title": "upsell"})


@router.post("/analyze", response_class=HTMLResponse)
def analyze_form(
    request: Request,
    folder: str = Form(...),
    name: str | None = Form(None),
    argus_run_id: int | None = Form(None),
    limit: int | None = Form(None),
):
    path = Path(folder).expanduser()
    try:
        result = service.analyze_folder(path, name=name, argus_run_id=argus_run_id, limit=limit)
    except FileNotFoundError:
        return templates.TemplateResponse(
            request,
            "index.html",
            {"error": f"Folder not found: {folder}", "runs": db.list_runs(limit=10)},
            status_code=400,
        )
    return RedirectResponse(f"/runs/{result['run_id']}", status_code=303)


@router.get("/runs/{run_id}", response_class=HTMLResponse)
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
        gallery_theme=payload.get("gallery_theme"),
        argus_run_id=row.get("argus_run_id"),
    )
    gallery = db.get_gallery(row["gallery_id"])
    mise_gallery_id = gallery.get("mise_gallery_id") if gallery else None
    mise_gallery_url = (
        f"{config.MISE_ADMIN_URL}/admin/galleries/{mise_gallery_id}"
        if config.MISE_ADMIN_URL and mise_gallery_id
        else None
    )
    bundles = enrich_bundles_for_run(run_id, payload.get("bundles") or [])
    top_photos = enrich_top_photos_for_run(run_id, payload.get("top_photos") or [])
    return templates.TemplateResponse(
        request,
        "run.html",
        ui_context(
            request,
            run=row,
            mise_gallery_url=mise_gallery_url,
            bundles=bundles,
            top_photos=top_photos,
            photo_count=payload.get("photo_count", 0),
            estimated_total_cents=payload.get("estimated_total_cents", 0),
            gallery_theme=payload.get("gallery_theme"),
            pitch_text=pitch_text,
            title=f"run {run_id}",
            share_links=[],
            tenant=None,
        ),
    )


def _run_edit_context(request: Request, run_id: int, **extra):
    row = db.get_run(run_id)
    if not row:
        return None
    gallery_name = db.get_gallery_name(row["gallery_id"]) or f"Run {run_id}"
    payload = row["payload"]
    return ui_context(
        request,
        run=row,
        bundles=payload.get("bundles") or [],
        gallery_photos=photos_for_run(row),
        gallery_name=gallery_name,
        title=f"Edit run {run_id}",
        save_action="/ui/homelab/run-edit",
        sell_url=None,
        tenant_id=None,
        **extra,
    )


@router.get("/runs/{run_id}/edit", response_class=HTMLResponse)
def edit_run(request: Request, run_id: int):
    ctx_data = _run_edit_context(request, run_id)
    if not ctx_data:
        return HTMLResponse("Run not found", status_code=404)
    return templates.TemplateResponse(request, "run_edit.html", ctx_data)


@router.post("/ui/homelab/run-edit")
async def ui_homelab_run_edit(request: Request, run_id: int = Form(...)):
    try:
        from ..bundle_editor import parse_bundle_form

        save_run_edits(
            run_id=run_id,
            tenant_id=None,
            bundle_edits=parse_bundle_form(await request.form()),
        )
    except BundleEditError as exc:
        ctx_data = _run_edit_context(request, run_id, edit_error=str(exc))
        if not ctx_data:
            return HTMLResponse("Run not found", status_code=404)
        return templates.TemplateResponse(request, "run_edit.html", ctx_data, status_code=400)
    return RedirectResponse(f"/runs/{run_id}?edited=1", status_code=303)


@router.get("/runs/{run_id}/photo/{filename}")
def run_photo(run_id: int, filename: str, size: str = Query("thumb")):
    row = db.get_run(run_id)
    if not row:
        return Response(status_code=404)
    gallery = db.get_gallery(row["gallery_id"])
    try:
        path = resolve_photo_file(
            gallery=gallery,
            payload=row["payload"],
            filename=filename,
        )
        max_edge = FULL_MAX_EDGE if size == "full" else THUMB_MAX_EDGE
        data = render_jpeg(path, max_edge=max_edge)
    except GalleryMediaError:
        return Response(status_code=404)
    return Response(
        content=data,
        media_type="image/jpeg",
        headers={
            "Cache-Control": "private, max-age=3600",
            "Content-Disposition": f'inline; filename="{path.name}"',
        },
    )


@router.get("/runs/{run_id}/json", response_class=JSONResponse)
def run_json(run_id: int):
    row = db.get_run(run_id)
    if not row:
        raise HTTPException(status_code=404, detail="run not found")
    return row


@router.get("/runs/{run_id}/pitch.txt", response_class=PlainTextResponse)
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
        gallery_theme=payload.get("gallery_theme"),
        argus_run_id=row.get("argus_run_id"),
    )
