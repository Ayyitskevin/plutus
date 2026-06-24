"""Bundle editor — tweak runs before client offer links."""
from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app import config, db, tenants
from app.bundle_editor import apply_edits, photos_for_run, save_run_edits
from app.storefront import create_share_link, resolve_offer


@pytest.fixture()
def saas_client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(config, "SAAS_MODE", True)
    monkeypatch.setattr(config, "API_TOKEN", "admin-secret")
    monkeypatch.setattr(config, "TENANT_KEY_PEPPER", "pepper-secret")
    monkeypatch.setattr(config, "RATE_LIMIT_ENABLED", False)
    monkeypatch.setattr(config, "SAAS_PUBLIC_URL", "http://plutus.test")
    db.migrate()
    from app.main import app

    return TestClient(app)


def _tiny_jpeg() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (60, 40), color=(90, 120, 200)).save(buf, format="JPEG")
    return buf.getvalue()


def _studio_run(tenant_id: str = "studio") -> tuple[str, int]:
    tenants.create_tenant(tenant_id, name="Studio", store_slug=tenant_id)
    issued = tenants.issue_api_key(tenant_id)
    folder = config.DATA_DIR / "gallery"
    folder.mkdir(parents=True)
    (folder / "a.jpg").write_bytes(_tiny_jpeg())
    (folder / "b.jpg").write_bytes(_tiny_jpeg())
    from app import service

    result = service.analyze_folder(folder, name="Edit me", tenant_id=tenant_id)
    return issued["api_key"], result["run_id"]


def test_apply_edits_swaps_photo_and_disables_bundle(saas_client):
    _, run_id = _studio_run()
    run = db.get_run(run_id, tenant_id="studio")
    photos = photos_for_run(run)
    assert len(photos) >= 2
    alt = photos[1]["filename"]
    payload = apply_edits(
        run=run,
        tenant_id="studio",
        bundle_edits=[
            {
                "title": "Custom hero",
                "pitch": "New pitch",
                "enabled": True,
                "items": [{"photo_filename": alt}],
            },
            {
                "title": "Hidden trio",
                "pitch": "",
                "enabled": False,
                "items": [],
            },
            {
                "title": "Gift set",
                "pitch": "",
                "enabled": True,
                "items": [],
                "photo_slots": ["a.jpg", "b.jpg", "a.jpg"],
            },
        ],
    )
    assert payload["bundles"][0]["title"] == "Custom hero"
    assert payload["bundles"][0]["items"][0]["photo"]["filename"] == alt
    assert payload["bundles"][1]["enabled"] is False


def test_storefront_skips_disabled_bundles(saas_client):
    _, run_id = _studio_run()
    run = db.get_run(run_id, tenant_id="studio")
    edits = []
    for i, bundle in enumerate(run["payload"]["bundles"]):
        edits.append({
            "title": bundle.get("title"),
            "pitch": bundle.get("pitch"),
            "enabled": i == 0,
            "items": [
                {"photo_filename": (item.get("photo") or {}).get("filename")}
                for item in bundle.get("items") or []
            ],
        })
    save_run_edits(run_id=run_id, tenant_id="studio", bundle_edits=edits)
    link = create_share_link(tenant_id="studio", run_id=run_id)
    offer = resolve_offer("studio", link["token"])
    assert len(offer["bundles"]) == 1


def test_ui_run_edit_flow(saas_client):
    api_key, run_id = _studio_run()
    saas_client.post(
        "/ui/saas/login",
        data={"api_token": api_key},
        follow_redirects=False,
    )
    edit_page = saas_client.get(f"/runs/{run_id}/edit")
    assert edit_page.status_code == 200
    assert b"Edit bundles" in edit_page.content
    run = db.get_run(run_id, tenant_id="studio")
    bundle_count = len(run["payload"]["bundles"])
    form = {"run_id": str(run_id)}
    for bi, bundle in enumerate(run["payload"]["bundles"]):
        form[f"b{bi}_enabled"] = "on" if bi == 0 else ""
        form[f"b{bi}_title"] = f"Bundle {bi} edited"
        form[f"b{bi}_pitch"] = "Client-ready pitch"
        for ii, item in enumerate(bundle.get("items") or []):
            form[f"b{bi}_item{ii}_photo"] = item["photo"]["filename"]
    r = saas_client.post("/ui/saas/app/run-edit", data=form, follow_redirects=False)
    assert r.status_code == 303
    assert f"/runs/{run_id}" in r.headers["location"]
    updated = db.get_run(run_id, tenant_id="studio")
    assert updated["payload"]["bundles"][0]["title"] == "Bundle 0 edited"
    assert sum(1 for b in updated["payload"]["bundles"] if b.get("enabled", True)) == 1
    assert bundle_count >= 1