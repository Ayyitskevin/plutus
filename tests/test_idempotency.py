"""PR3 — one stable offer per gallery + correlation_id echo.

A re-run of the same Mise gallery must refresh the offer in place (same run_id,
no duplicate gallery/run rows, no duplicated bundles) — an invariant Mise relies
on. Mock-only and deterministic.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app import config, db, service


@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(config, "PUBLIC_URL", "http://plutus.test")
    monkeypatch.setattr(config, "MISE_MEDIA_ROOT", tmp_path / "mise-media")
    db.migrate()
    return tmp_path


def _gallery_row(tmp_path, gid: int, *, title: str = "Tasting Menu", photos: int = 3):
    folder = tmp_path / "mise-media" / str(gid) / "original"
    folder.mkdir(parents=True, exist_ok=True)
    for i in range(photos):
        Image.new("RGB", (90, 70)).save(folder / f"p{i}.jpg")
    return {
        "id": gid,
        "title": title,
        "published": True,
        "originals_path": str(folder),
        "argus_last_run_id": None,
    }


def _count(sql: str, params: tuple) -> int:
    with db.connection() as con:
        row = con.execute(sql, params).fetchone()
    return int(row["n"])


def test_rerun_reuses_run_and_creates_no_duplicates(tmp_db, tmp_path):
    gid = 11
    row = _gallery_row(tmp_path, gid)
    with patch("app.mise_client.get_gallery", return_value=row):
        with patch("app.mise_client.is_enabled", return_value=True):
            first = service.analyze_mise_gallery(gid)
            second = service.analyze_mise_gallery(gid)

    # Stable offer: same run_id both times.
    assert first["run_id"] == second["run_id"]
    # Exactly one gallery and one run for this Mise gallery.
    assert _count("SELECT COUNT(*) AS n FROM galleries WHERE mise_gallery_id=?", (gid,)) == 1
    assert (
        _count(
            "SELECT COUNT(*) AS n FROM recommendation_runs WHERE gallery_id=?",
            (first["gallery_id"],),
        )
        == 1
    )
    # Bundles refreshed in place, not duplicated.
    assert len(first["bundles"]) == len(second["bundles"])
    stored = db.get_run(first["run_id"])
    assert len(stored["payload"]["bundles"]) == len(first["bundles"])


def test_rerun_refreshes_offer_content(tmp_db, tmp_path):
    gid = 12
    small = _gallery_row(tmp_path, gid, photos=3)
    big = _gallery_row(tmp_path, gid, photos=8)
    with patch("app.mise_client.is_enabled", return_value=True):
        with patch("app.mise_client.get_gallery", return_value=small):
            first = service.analyze_mise_gallery(gid)
        # More photos on the re-run → refreshed photo_count, same run_id.
        with patch("app.mise_client.get_gallery", return_value=big):
            second = service.analyze_mise_gallery(gid)

    assert first["run_id"] == second["run_id"]
    assert second["photo_count"] == 8
    stored = db.get_run(first["run_id"])
    assert stored["payload"]["photo_count"] == 8


def test_distinct_galleries_get_distinct_offers(tmp_db, tmp_path):
    with patch("app.mise_client.is_enabled", return_value=True):
        with patch("app.mise_client.get_gallery", return_value=_gallery_row(tmp_path, 21)):
            a = service.analyze_mise_gallery(21)
        with patch("app.mise_client.get_gallery", return_value=_gallery_row(tmp_path, 22)):
            b = service.analyze_mise_gallery(22)
    assert a["run_id"] != b["run_id"]
    assert a["gallery_id"] != b["gallery_id"]


def test_correlation_id_is_echoed(tmp_db, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "API_TOKEN", "studio-admin")
    monkeypatch.setattr(config, "MISE_HOOK_TOKEN", None)
    monkeypatch.setattr(config, "SERVICE_TOKENS", [])
    from app.main import app

    client = TestClient(app)
    row = _gallery_row(tmp_path, 31)
    with patch("app.mise_client.get_gallery", return_value=row):
        with patch("app.mise_client.is_enabled", return_value=True):
            r = client.post(
                "/recommend/mise-gallery",
                data={"mise_gallery_id": 31, "correlation_id": "mise-corr-abc"},
                headers={"Authorization": "Bearer studio-admin"},
            )
    assert r.status_code == 200
    assert r.json()["correlation_id"] == "mise-corr-abc"


def test_no_correlation_id_when_absent(tmp_db, tmp_path):
    gid = 41
    row = _gallery_row(tmp_path, gid)
    with patch("app.mise_client.get_gallery", return_value=row):
        with patch("app.mise_client.is_enabled", return_value=True):
            result = service.analyze_mise_gallery(gid)
    assert "correlation_id" not in result
