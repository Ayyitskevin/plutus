"""The operational + safety contract, asserted in one place.

Under the conftest network guard (so it is *proven* mock-only), a Mise gallery runs
through the live recommend path and the resulting offer must:
  * validate against the offers schema — run_id, bundles with a stable sku, a
    reconciling estimated_total_cents (the schema ask);
  * be idempotent — a re-run refreshes one offer per gallery, never a duplicate
    (the idempotency ask);
  * be free and deterministic — cost_usd == 0.0, the rules-engine model, and zero
    external network connections (the mock-only ask).

This complements the focused suites (test_offer_contract / test_idempotency) by tying
the three guarantees together with the no-live-call enforcement.
"""
from __future__ import annotations

import socket
from unittest.mock import patch

import pytest
from conftest import BlockedNetworkCall
from PIL import Image

from app import config, db, offer_schema, recommend, service


@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(config, "PUBLIC_URL", "http://plutus.test")
    monkeypatch.setattr(config, "MISE_MEDIA_ROOT", tmp_path / "mise-media")
    db.migrate()
    return tmp_path


def _gallery_row(tmp_path, gid: int, *, photos: int = 14) -> dict:
    folder = tmp_path / "mise-media" / str(gid) / "original"
    folder.mkdir(parents=True, exist_ok=True)
    for i in range(photos):
        Image.new("RGB", (120, 90)).save(folder / f"img_{i:03d}.jpg")
    return {
        "id": gid,
        "title": "Tasting Menu",
        "published": True,
        "originals_path": str(folder),
        "argus_last_run_id": None,
    }


def _count(sql: str, params: tuple) -> int:
    with db.connection() as con:
        return int(con.execute(sql, params).fetchone()["n"])


def test_offer_validates_and_run_is_free_and_offline(tmp_db, tmp_path, network_blocked):
    gid = 101
    row = _gallery_row(tmp_path, gid)
    with patch("app.mise_client.get_gallery", return_value=row):
        with patch("app.mise_client.is_enabled", return_value=True):
            offer = service.analyze_mise_gallery(gid)

    # Schema ask: validates clean against the canonical contract.
    assert offer_schema.validate_offer(offer) == [], offer_schema.validate_offer(offer)
    assert isinstance(offer["run_id"], int) and offer["run_id"] >= 1
    assert offer["bundles"], "expected at least one bundle"
    for bundle in offer["bundles"]:
        assert bundle["sku"], "every bundle needs a stable contract sku"
        assert bundle["estimated_cents"] == sum(
            li["qty"] * li["unit_cents"] for li in bundle["line_items"]
        )
    assert offer["estimated_total_cents"] == sum(
        b["estimated_cents"] for b in offer["bundles"]
    )

    # Mock-only ask: deterministic rules engine, no paid model, no live calls.
    assert offer["cost_usd"] == 0.0
    assert offer["model"] == recommend.MODEL_VERSION
    assert network_blocked == [], f"recommend path reached the network: {network_blocked}"


def test_rerun_is_idempotent_under_guard(tmp_db, tmp_path, network_blocked):
    gid = 102
    row = _gallery_row(tmp_path, gid)
    with patch("app.mise_client.get_gallery", return_value=row):
        with patch("app.mise_client.is_enabled", return_value=True):
            first = service.analyze_mise_gallery(gid)
            second = service.analyze_mise_gallery(gid)

    # Idempotency ask: one stable offer per gallery, refreshed in place.
    assert first["run_id"] == second["run_id"]
    assert _count("SELECT COUNT(*) AS n FROM galleries WHERE mise_gallery_id=?", (gid,)) == 1
    assert (
        _count(
            "SELECT COUNT(*) AS n FROM recommendation_runs WHERE gallery_id=?",
            (first["gallery_id"],),
        )
        == 1
    )
    assert network_blocked == [], f"recommend path reached the network: {network_blocked}"


def test_healthz_makes_no_live_calls(tmp_db, network_blocked, monkeypatch):
    monkeypatch.setattr(config, "API_TOKEN", "studio-admin")
    from fastapi.testclient import TestClient

    from app.main import app

    body = TestClient(app).get("/healthz").json()
    assert body["service"] == "plutus"
    assert body["offer_schema_version"] == offer_schema.OFFER_SCHEMA_VERSION
    assert body["model"] == recommend.MODEL_VERSION
    assert "status" in body and "checks" in body
    assert network_blocked == [], f"/healthz reached the network: {network_blocked}"


def test_guard_blocks_external_but_allows_loopback(network_blocked):
    # A non-loopback connect is refused before any packet leaves (TEST-NET-3 addr).
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        with pytest.raises(BlockedNetworkCall):
            sock.connect(("203.0.113.1", 80))
    finally:
        sock.close()
    assert "203.0.113.1" in network_blocked

    # Loopback is permitted (guard is selective, not a blanket block): connecting to
    # a closed local port reaches the real stack and fails with a normal OSError.
    lo = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    lo.settimeout(0.2)
    try:
        with pytest.raises(OSError) as exc:
            lo.connect(("127.0.0.1", 9))
        assert not isinstance(exc.value, BlockedNetworkCall)
    finally:
        lo.close()
