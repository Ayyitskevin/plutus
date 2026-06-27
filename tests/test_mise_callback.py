"""PR5 — the config-gated Mise callback push (contract point #3, async half)."""
from __future__ import annotations

from unittest.mock import patch

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app import config, db, mise_client


@pytest.fixture(autouse=True)
def _callback_config(monkeypatch):
    monkeypatch.setattr(config, "MISE_URL", "http://mise.test")
    monkeypatch.setattr(config, "MISE_API_TOKEN", "mise-tok")
    monkeypatch.setattr(config, "MISE_CALLBACK_URL", None)
    monkeypatch.setattr(config, "MISE_CALLBACK_TOKEN", None)
    monkeypatch.setattr(config, "MISE_CALLBACK_ENABLED", True)


class _FakeResp:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code


def _fake_client(captured: dict, *, status_code: int = 200, raises: Exception | None = None):
    class FakeClient:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def post(self, url, *, params, json, headers):
            captured.update(url=url, params=params, json=json, headers=headers)
            if raises is not None:
                raise raises
            return _FakeResp(status_code)

    return FakeClient


# --- unit behavior -----------------------------------------------------------


def test_disabled_by_default(monkeypatch):
    monkeypatch.setattr(config, "MISE_CALLBACK_ENABLED", False)
    assert mise_client.callback_enabled() is False
    captured: dict = {}
    monkeypatch.setattr(mise_client.httpx, "Client", _fake_client(captured))
    out = mise_client.post_offer_callback(gallery_id=7, payload={"run_id": 1})
    assert out == {"status": "disabled"}
    assert captured == {}  # no HTTP call


def test_delivered_with_correlation_and_bearer(monkeypatch):
    captured: dict = {}
    monkeypatch.setattr(mise_client.httpx, "Client", _fake_client(captured))
    out = mise_client.post_offer_callback(
        gallery_id=7, payload={"run_id": 1, "bundles": []}, correlation_id="corr-9"
    )
    assert out["status"] == "delivered"
    assert captured["url"] == "http://mise.test/api/plutus/callback"
    assert captured["params"] == {"gallery_id": 7}
    assert captured["headers"]["Authorization"] == "Bearer mise-tok"
    assert captured["json"]["run_id"] == 1
    assert captured["json"]["correlation_id"] == "corr-9"


def test_unknown_subject_is_noop(monkeypatch):
    captured: dict = {}
    monkeypatch.setattr(mise_client.httpx, "Client", _fake_client(captured, status_code=404))
    out = mise_client.post_offer_callback(gallery_id=7, payload={"run_id": 1})
    assert out["status"] == "ignored"  # no-op, not an error


def test_transport_failure_is_swallowed(monkeypatch):
    captured: dict = {}
    monkeypatch.setattr(
        mise_client.httpx,
        "Client",
        _fake_client(captured, raises=httpx.ConnectError("boom")),
    )
    out = mise_client.post_offer_callback(gallery_id=7, payload={"run_id": 1})
    assert out["status"] == "error"  # never raises


def test_callback_token_override(monkeypatch):
    monkeypatch.setattr(config, "MISE_CALLBACK_URL", "http://callback.test")
    monkeypatch.setattr(config, "MISE_CALLBACK_TOKEN", "cb-tok")
    captured: dict = {}
    monkeypatch.setattr(mise_client.httpx, "Client", _fake_client(captured))
    mise_client.post_offer_callback(gallery_id=3, payload={"run_id": 1})
    assert captured["url"] == "http://callback.test/api/plutus/callback"
    assert captured["headers"]["Authorization"] == "Bearer cb-tok"


# --- recommend-path integration ----------------------------------------------


@pytest.fixture()
def studio_client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(config, "API_TOKEN", "studio-admin")
    monkeypatch.setattr(config, "MISE_HOOK_TOKEN", None)
    monkeypatch.setattr(config, "SERVICE_TOKENS", [])
    monkeypatch.setattr(config, "PUBLIC_URL", "http://plutus.test")
    monkeypatch.setattr(config, "RATE_LIMIT_ENABLED", False)
    monkeypatch.setattr(config, "MISE_MEDIA_ROOT", tmp_path / "mise-media")
    db.migrate()
    from app.main import app

    return TestClient(app)


def _gallery(tmp_path, gid=5):
    folder = tmp_path / "mise-media" / str(gid) / "original"
    folder.mkdir(parents=True)
    Image.new("RGB", (80, 60)).save(folder / "a.jpg")
    return {"id": gid, "title": "G", "published": True, "originals_path": str(folder),
            "argus_last_run_id": None}


def test_recommend_fires_callback_when_enabled(studio_client, tmp_path):
    captured: dict = {}
    with patch("app.mise_client.get_gallery", return_value=_gallery(tmp_path)):
        with patch("app.mise_client.is_enabled", return_value=True):
            with patch.object(mise_client.httpx, "Client", _fake_client(captured)):
                r = studio_client.post(
                    "/recommend/mise-gallery",
                    data={"mise_gallery_id": 5, "correlation_id": "abc"},
                    headers={"Authorization": "Bearer studio-admin"},
                )
    assert r.status_code == 200
    body = r.json()
    assert body["callback"]["status"] == "delivered"
    assert captured["params"] == {"gallery_id": 5}
    assert captured["json"]["correlation_id"] == "abc"
    assert captured["json"]["run_id"] == body["run_id"]


def test_recommend_survives_callback_failure(studio_client, tmp_path):
    captured: dict = {}
    failing = _fake_client(captured, raises=httpx.ConnectError("down"))
    with patch("app.mise_client.get_gallery", return_value=_gallery(tmp_path, gid=6)):
        with patch("app.mise_client.is_enabled", return_value=True):
            with patch.object(mise_client.httpx, "Client", failing):
                r = studio_client.post(
                    "/recommend/mise-gallery",
                    data={"mise_gallery_id": 6},
                    headers={"Authorization": "Bearer studio-admin"},
                )
    # Recommend still succeeds; the callback failure is recorded, not raised.
    assert r.status_code == 200
    assert r.json()["callback"]["status"] == "error"


def test_recommend_omits_callback_when_disabled(studio_client, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "MISE_CALLBACK_ENABLED", False)
    with patch("app.mise_client.get_gallery", return_value=_gallery(tmp_path, gid=8)):
        with patch("app.mise_client.is_enabled", return_value=True):
            r = studio_client.post(
                "/recommend/mise-gallery",
                data={"mise_gallery_id": 8},
                headers={"Authorization": "Bearer studio-admin"},
            )
    assert r.status_code == 200
    assert "callback" not in r.json()  # backward-compatible default
