"""Hardened Mise callback — idempotency, 401 re-auth, retry + dead-letter.

Mock-only: httpx is replaced with a scripted fake and time.sleep is neutered, so
the retry/backoff and dead-letter paths run without network or real delays.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app import config, db, mise_callback


@pytest.fixture(autouse=True)
def _callback_config(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(config, "MISE_URL", "http://mise.test")
    monkeypatch.setattr(config, "MISE_API_TOKEN", "mise-tok")
    monkeypatch.setattr(config, "MISE_CALLBACK_URL", None)
    monkeypatch.setattr(config, "MISE_CALLBACK_TOKEN", None)
    monkeypatch.setattr(config, "MISE_CALLBACK_ENABLED", True)
    monkeypatch.setattr(config, "MISE_CALLBACK_MAX_ATTEMPTS", 3)
    monkeypatch.setattr(config, "MISE_CALLBACK_BACKOFF_BASE", 0.0)
    monkeypatch.setattr(mise_callback.time, "sleep", lambda *_: None)  # no real backoff waits
    db.migrate()


class _Resp:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code


def _scripted_client(calls: list, *, statuses=None, raises_each=None):
    """FakeClient whose .post replays per-call statuses or raises per-call errors."""
    statuses = list(statuses or [])
    raises_each = list(raises_each or [])

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def post(self, url, *, params, json, headers):
            i = len(calls)
            calls.append({"url": url, "params": params, "json": json, "headers": headers})
            if i < len(raises_each) and raises_each[i] is not None:
                raise raises_each[i]
            return _Resp(statuses[i] if i < len(statuses) else statuses[-1])

    return FakeClient


def _payload():
    return {"run_id": 1, "estimated_total_cents": 100, "bundles": []}


# --- idempotency -------------------------------------------------------------


def test_idempotency_key_is_stable():
    assert mise_callback.idempotency_key(7, 42) == "plutus-offer-7-42"
    assert mise_callback.idempotency_key(7, 42) == mise_callback.idempotency_key(7, 42)


def test_delivered_sends_idempotency_key_and_bearer(monkeypatch):
    calls: list = []
    monkeypatch.setattr(mise_callback.httpx, "Client", _scripted_client(calls, statuses=[200]))
    out = mise_callback.deliver(gallery_id=7, run_id=42, payload=_payload(), correlation_id="c1")
    assert out["status"] == "delivered"
    assert calls[0]["headers"]["Idempotency-Key"] == "plutus-offer-7-42"
    assert calls[0]["headers"]["Authorization"] == "Bearer mise-tok"
    assert calls[0]["params"] == {"gallery_id": 7}
    assert calls[0]["json"]["idempotency_key"] == "plutus-offer-7-42"
    assert calls[0]["json"]["correlation_id"] == "c1"


def test_redelivery_does_not_duplicate(monkeypatch):
    # Two failing deliveries for the same (gallery, run) → exactly one outbox row.
    calls: list = []
    monkeypatch.setattr(mise_callback.httpx, "Client", _scripted_client(calls, statuses=[503]))
    mise_callback.deliver(gallery_id=7, run_id=42, payload=_payload())
    mise_callback.deliver(gallery_id=7, run_id=42, payload=_payload())
    assert db.count_callback_deadletter() == 1
    assert db.get_callback_deadletter("plutus-offer-7-42") is not None


def test_unknown_subject_is_noop(monkeypatch):
    calls: list = []
    monkeypatch.setattr(mise_callback.httpx, "Client", _scripted_client(calls, statuses=[404]))
    out = mise_callback.deliver(gallery_id=7, run_id=42, payload=_payload())
    assert out["status"] == "ignored"
    assert db.count_callback_deadletter() == 0  # no-op, not dead-lettered


# --- auth / 401 re-auth ------------------------------------------------------


def test_401_refreshes_token_and_retries(monkeypatch):
    calls: list = []
    # First call 401, refreshed-token call 200.
    monkeypatch.setattr(mise_callback.httpx, "Client", _scripted_client(calls, statuses=[401, 200]))
    monkeypatch.setattr(mise_callback, "_refresh_callback_token", lambda: "rotated-tok")
    out = mise_callback.deliver(gallery_id=7, run_id=42, payload=_payload())
    assert out["status"] == "delivered"
    assert len(calls) == 2
    assert calls[1]["headers"]["Authorization"] == "Bearer rotated-tok"  # retried with new token
    assert db.count_callback_deadletter() == 0


def test_hard_401_is_dead_lettered_and_surfaced(monkeypatch):
    calls: list = []
    monkeypatch.setattr(mise_callback.httpx, "Client", _scripted_client(calls, statuses=[401, 401]))
    monkeypatch.setattr(mise_callback, "_refresh_callback_token", lambda: "still-bad")
    with patch.object(mise_callback.log, "error") as err:
        out = mise_callback.deliver(gallery_id=7, run_id=42, payload=_payload())
    assert out["status"] == "auth_failed"  # never silently dropped
    assert err.called  # surfaced
    row = db.get_callback_deadletter("plutus-offer-7-42")
    assert row is not None and row["last_status"] == "unauthorized"


# --- retry + backoff + dead-letter ------------------------------------------


def test_transient_failures_retry_then_dead_letter(monkeypatch):
    calls: list = []
    import httpx

    monkeypatch.setattr(
        mise_callback.httpx,
        "Client",
        _scripted_client(calls, raises_each=[httpx.ConnectError("x")] * 3, statuses=[503]),
    )
    sleeps: list = []
    monkeypatch.setattr(mise_callback.time, "sleep", lambda s: sleeps.append(s))
    monkeypatch.setattr(config, "MISE_CALLBACK_BACKOFF_BASE", 0.5)
    out = mise_callback.deliver(gallery_id=7, run_id=42, payload=_payload())
    assert out["status"] == "dead_lettered"
    assert out["attempts"] == 3  # MAX_ATTEMPTS
    assert len(calls) == 3
    assert sleeps == [0.5, 1.0]  # exponential backoff between the 3 attempts
    assert db.count_callback_deadletter() == 1


def test_redeliver_pending_clears_on_success(monkeypatch):
    # Dead-letter one, then a healthy endpoint clears it on re-delivery.
    monkeypatch.setattr(mise_callback.httpx, "Client", _scripted_client([], statuses=[503]))
    mise_callback.deliver(gallery_id=7, run_id=42, payload=_payload())
    assert db.count_callback_deadletter() == 1

    monkeypatch.setattr(mise_callback.httpx, "Client", _scripted_client([], statuses=[200]))
    summary = mise_callback.redeliver_pending()
    assert summary["attempted"] == 1
    assert summary["results"][0]["status"] == "delivered"
    assert db.count_callback_deadletter() == 0


def test_disabled_is_a_noop(monkeypatch):
    monkeypatch.setattr(config, "MISE_CALLBACK_ENABLED", False)
    calls: list = []
    monkeypatch.setattr(mise_callback.httpx, "Client", _scripted_client(calls, statuses=[200]))
    out = mise_callback.deliver(gallery_id=7, run_id=42, payload=_payload())
    assert out == {"status": "disabled"}
    assert calls == []


# --- recommend-path integration ----------------------------------------------


@pytest.fixture()
def studio_client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "API_TOKEN", "studio-admin")
    monkeypatch.setattr(config, "MISE_HOOK_TOKEN", None)
    monkeypatch.setattr(config, "SERVICE_TOKENS", [])
    monkeypatch.setattr(config, "PUBLIC_URL", "http://plutus.test")
    monkeypatch.setattr(config, "MISE_MEDIA_ROOT", tmp_path / "mise-media")
    from app.main import app

    return TestClient(app)


def _gallery(tmp_path, gid):
    folder = tmp_path / "mise-media" / str(gid) / "original"
    folder.mkdir(parents=True)
    Image.new("RGB", (80, 60)).save(folder / "a.jpg")
    return {"id": gid, "title": "G", "published": True, "originals_path": str(folder),
            "argus_last_run_id": None}


def test_recommend_survives_callback_dead_letter(studio_client, tmp_path, monkeypatch):
    import httpx

    monkeypatch.setattr(
        mise_callback.httpx, "Client",
        _scripted_client([], raises_each=[httpx.ConnectError("down")] * 5, statuses=[503]),
    )
    with patch("app.mise_client.get_gallery", return_value=_gallery(tmp_path, 9)):
        with patch("app.mise_client.is_enabled", return_value=True):
            r = studio_client.post(
                "/recommend/mise-gallery",
                data={"mise_gallery_id": 9},
                headers={"Authorization": "Bearer studio-admin"},
            )
    assert r.status_code == 200  # recommend never crashes on callback failure
    assert r.json()["callback"]["status"] == "dead_lettered"


def test_callback_payload_validates_against_offer_schema(studio_client, tmp_path):
    from app import offer_schema

    calls: list = []
    ok_client = _scripted_client(calls, statuses=[200])
    with patch("app.mise_client.get_gallery", return_value=_gallery(tmp_path, 11)):
        with patch("app.mise_client.is_enabled", return_value=True):
            with patch.object(mise_callback.httpx, "Client", ok_client):
                r = studio_client.post(
                    "/recommend/mise-gallery",
                    data={"mise_gallery_id": 11, "correlation_id": "c9"},
                    headers={"Authorization": "Bearer studio-admin"},
                )
    assert r.status_code == 200
    # The exact JSON bytes Plutus PUTs on the wire must satisfy the offer contract.
    body = calls[0]["json"]
    assert offer_schema.validate_offer(body) == [], offer_schema.validate_offer(body)
    assert body["idempotency_key"] == "plutus-offer-11-" + str(r.json()["run_id"])
    assert body["correlation_id"] == "c9"
