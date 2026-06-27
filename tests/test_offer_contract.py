"""PR1 — the Mise offer contract: stable bundle SKU + line_items + provenance.

Mock-only and deterministic: a fixed photo set runs through the rules engine and
the resulting offer is validated against the canonical contract schema.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest
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


def _photos(n: int = 14) -> list[dict]:
    # Deterministic, vision-enriched gallery — enough keepers to trigger an album.
    return [
        {
            "filename": f"img_{i:03d}.jpg",
            "path": f"/m/img_{i:03d}.jpg",
            "keeper_score": 0.8,
            "hero_potential": 0.9 if i == 0 else 0.6,
            "shot_type": "hero_plate" if i == 0 else "detail",
            "orientation": "landscape" if i == 0 else "square",
            "keywords": ["food", "plating"],
        }
        for i in range(n)
    ]


def test_recommend_bundles_carry_stable_sku_and_line_items():
    payload = recommend.recommend_bundles(_photos())
    assert payload["bundles"], "expected at least one bundle"
    for bundle in payload["bundles"]:
        assert bundle["sku"], "bundle needs a stable contract sku"
        assert bundle["sku"] == bundle["id"], "sku should mirror the stable bundle id"
        assert bundle["label"] == bundle["title"]
        assert bundle["line_items"], "bundle needs line_items"
        # estimated_cents is the sum of its line items (proposal only).
        assert bundle["estimated_cents"] == sum(
            li["qty"] * li["unit_cents"] for li in bundle["line_items"]
        )
        for line in bundle["line_items"]:
            assert {"sku", "label", "qty", "unit_cents"} <= set(line)


def test_provenance_present_and_cost_is_free_for_rules_engine():
    payload = recommend.recommend_bundles(_photos())
    assert payload["model"] == recommend.MODEL_VERSION
    assert isinstance(payload["latency_ms"], int) and payload["latency_ms"] >= 0
    assert payload["cost_usd"] == 0.0


def test_empty_gallery_still_reports_provenance():
    payload = recommend.recommend_bundles([])
    assert payload["bundles"] == []
    assert payload["model"] == recommend.MODEL_VERSION
    assert "latency_ms" in payload and "cost_usd" in payload


def test_full_offer_validates_against_contract(tmp_db, tmp_path):
    gid = 9
    folder = tmp_path / "mise-media" / str(gid) / "original"
    folder.mkdir(parents=True)
    for i in range(14):
        Image.new("RGB", (120, 90)).save(folder / f"img_{i:03d}.jpg")

    row = {
        "id": gid,
        "title": "Tasting Menu",
        "published": True,
        "originals_path": str(folder),
        "argus_last_run_id": None,
    }
    with patch("app.mise_client.get_gallery", return_value=row):
        with patch("app.mise_client.is_enabled", return_value=True):
            offer = service.analyze_mise_gallery(gid)

    problems = offer_schema.validate_offer(offer)
    assert problems == [], f"offer violates contract: {problems}"
    # Contract headline fields are all present.
    assert offer["offer_url"].endswith(f"/runs/{offer['run_id']}")
    assert offer["pitch_url"].endswith("/pitch.txt")


def test_validator_rejects_drifted_total():
    bad = {
        "run_id": 1,
        "estimated_total_cents": 999,  # wrong on purpose
        "offer_url": "http://x/runs/1",
        "pitch_url": "http://x/runs/1/pitch.txt",
        "model": "plutus-rules-v1",
        "latency_ms": 1,
        "cost_usd": 0.0,
        "bundles": [
            {
                "sku": "wall-hero",
                "label": "Statement wall piece",
                "estimated_cents": 18500,
                "line_items": [
                    {"sku": "canvas-16x20", "label": "Canvas Wrap", "qty": 1, "unit_cents": 18500}
                ],
            }
        ],
    }
    assert offer_schema.validate_offer(bad), "validator must catch total drift"


def test_to_mise_offer_is_exact_strict_shape(tmp_db, tmp_path):
    """The canonical projection is EXACTLY the contract keys — no UI extras."""
    gid = 14
    folder = tmp_path / "mise-media" / str(gid) / "original"
    folder.mkdir(parents=True)
    for i in range(14):
        Image.new("RGB", (120, 90)).save(folder / f"img_{i:03d}.jpg")
    row = {
        "id": gid,
        "title": "Tasting Menu",
        "published": True,
        "originals_path": str(folder),
        "argus_last_run_id": None,
    }
    with patch("app.mise_client.get_gallery", return_value=row):
        with patch("app.mise_client.is_enabled", return_value=True):
            result = service.analyze_mise_gallery(gid, correlation_id="corr-1")

    offer = offer_schema.to_mise_offer(result)

    # Exact top-level shape (correlation_id echoed when present).
    assert set(offer) == set(offer_schema.OFFER_KEYS) | {"correlation_id"}
    assert offer["correlation_id"] == "corr-1"
    assert offer["run_id"] == result["run_id"]
    # No UI/superset keys leaked into the strict view.
    for leaked in ("engine", "review_url", "top_photos", "gallery_theme", "bundle_count"):
        assert leaked not in offer

    assert offer["bundles"], "expected bundles"
    for bundle in offer["bundles"]:
        assert set(bundle) == set(offer_schema.BUNDLE_KEYS)
        for line in bundle["line_items"]:
            assert set(line) == set(offer_schema.LINE_ITEM_KEYS)
        # Per-bundle cents reconcile to the line items (proposal money is 1:1).
        assert bundle["estimated_cents"] == sum(
            li["qty"] * li["unit_cents"] for li in bundle["line_items"]
        )

    # Validates clean, and totals reconcile across the whole offer.
    assert offer_schema.validate_offer(offer) == []
    assert offer["estimated_total_cents"] == sum(b["estimated_cents"] for b in offer["bundles"])


def test_to_mise_offer_omits_correlation_when_absent(tmp_db, tmp_path):
    gid = 15
    folder = tmp_path / "mise-media" / str(gid) / "original"
    folder.mkdir(parents=True)
    Image.new("RGB", (120, 90)).save(folder / "a.jpg")
    row = {
        "id": gid,
        "title": "G",
        "published": True,
        "originals_path": str(folder),
        "argus_last_run_id": None,
    }
    with patch("app.mise_client.get_gallery", return_value=row):
        with patch("app.mise_client.is_enabled", return_value=True):
            result = service.analyze_mise_gallery(gid)
    offer = offer_schema.to_mise_offer(result)
    assert "correlation_id" not in offer
    assert set(offer) == set(offer_schema.OFFER_KEYS)
