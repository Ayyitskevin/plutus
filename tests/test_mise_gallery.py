from unittest.mock import patch

import pytest

from app import config, db, service


@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(
        config, "MISE_MEDIA_ROOT", tmp_path / "mise-media"
    )
    db.migrate()
    return tmp_path


def test_analyze_mise_gallery(tmp_db, tmp_path):
    gid = 7
    folder = tmp_path / "mise-media" / str(gid) / "original"
    folder.mkdir(parents=True)
    from PIL import Image

    Image.new("RGB", (100, 80)).save(folder / "a.jpg")

    row = {
        "id": gid,
        "title": "Tasting Menu",
        "published": True,
        "originals_path": str(folder),
        "argus_last_run_id": None,
    }
    with patch("app.mise_client.get_gallery", return_value=row):
        with patch("app.mise_client.is_enabled", return_value=True):
            result = service.analyze_mise_gallery(gid)

    assert result["mise_gallery_id"] == gid
    assert result["run_id"] >= 1
    assert len(result["bundles"]) >= 1
    assert f"/runs/{result['run_id']}" in result["review_url"]
    assert result["pitch_url"].endswith("/pitch.txt")