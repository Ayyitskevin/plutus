import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app import config, db


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(config, "SAAS_MODE", False)
    monkeypatch.setattr(config, "API_TOKEN", "")
    db.migrate()
    from app.main import app

    return TestClient(app)


def test_healthz(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json()["service"] == "plutus"


def test_analyze_demo_folder(client, tmp_path):
    folder = tmp_path / "gallery"
    folder.mkdir()
    Image.new("RGB", (80, 60), color=(120, 90, 40)).save(folder / "01.jpg")

    r = client.post(
        "/analyze-folder",
        data={"folder": str(folder), "name": "smoke"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["run_id"] >= 1
    assert len(body.get("bundles") or []) >= 1

    run = client.get(f"/runs/{body['run_id']}")
    assert run.status_code == 200
    assert b"Upsell bundles" in run.content


def test_studio_run_urls(monkeypatch):
    monkeypatch.setattr(config, "PUBLIC_URL", "http://plutus.test")
    from app.service import studio_run_urls

    assert studio_run_urls(5) == {
        "review_url": "http://plutus.test/runs/5",
        "pitch_url": "http://plutus.test/runs/5/pitch.txt",
    }


def test_run_page_renders_photo_thumbnails(client, tmp_path):
    folder = tmp_path / "gallery"
    folder.mkdir()
    Image.new("RGB", (120, 90), color=(200, 40, 40)).save(folder / "hero.jpg")

    created = client.post(
        "/analyze-folder",
        data={"folder": str(folder), "name": "Thumb test"},
    )
    run_id = created.json()["run_id"]
    page = client.get(f"/runs/{run_id}")
    assert page.status_code == 200
    assert f"/runs/{run_id}/photo/hero.jpg" in page.text

    photo = client.get(f"/runs/{run_id}/photo/hero.jpg")
    assert photo.status_code == 200
    assert photo.headers["content-type"].startswith("image/jpeg")
    assert len(photo.content) > 100