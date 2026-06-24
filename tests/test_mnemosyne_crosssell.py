"""Mnemosyne cross-sell on client offer pages."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import catalog, config, db, tenants


@pytest.fixture()
def saas_client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(config, "SAAS_MODE", True)
    monkeypatch.setattr(config, "API_TOKEN", "admin-secret")
    monkeypatch.setattr(config, "RATE_LIMIT_ENABLED", False)
    monkeypatch.setattr(config, "SAAS_PUBLIC_URL", "http://plutus.test")
    db.migrate()
    from app.main import app

    return TestClient(app)


def test_bundles_include_album_detects_album_sku():
    bundles = [
        {"items": [{"sku": "print-8x10", "label": "Print"}]},
        {"items": [{"sku": "album-20", "label": "Album"}]},
    ]
    assert catalog.bundles_include_album(bundles) is True
    assert catalog.bundles_include_album([{"items": [{"sku": "print-8x10"}]}]) is False


def test_store_offer_shows_mnemosyne_cta_for_album_bundle(saas_client: TestClient, monkeypatch):
    monkeypatch.setattr(config, "MNEMOSYNE_URL", "https://mnemosyne.test")
    tenants.create_tenant("albumco", name="Album Co", store_slug="album-co")
    gid = db.insert_gallery(name="Wedding", source="/x", photo_count=1, tenant_id="albumco")
    rid = db.insert_run(
        gallery_id=gid,
        engine="mock",
        bundle_count=1,
        estimated_total_cents=28500,
        payload={
            "bundles": [
                {
                    "title": "Album package",
                    "enabled": True,
                    "items": [
                        {
                            "sku": "album-20",
                            "label": "Layflat Album",
                            "unit_cents": 28500,
                            "quantity": 1,
                            "photo": "01.jpg",
                        }
                    ],
                }
            ]
        },
        tenant_id="albumco",
    )
    link = db.create_storefront_token(
        token="album-offer",
        tenant_id="albumco",
        run_id=rid,
        label="Album",
    )
    r = saas_client.get(f"/store/album-co/offer/{link['token']}")
    assert r.status_code == 200
    assert "mnemosyne.test" in r.text
    assert "Album design" in r.text