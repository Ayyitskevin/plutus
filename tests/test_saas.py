import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app import config, db, tenants


@pytest.fixture()
def saas_client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(config, "SAAS_MODE", True)
    monkeypatch.setattr(config, "API_TOKEN", "admin-secret")
    monkeypatch.setattr(config, "TENANT_KEY_PEPPER", "pepper-secret")
    monkeypatch.setattr(config, "RATE_LIMIT_ENABLED", False)
    monkeypatch.setattr(config, "UPLOAD_ASYNC_ANALYZE", False)
    db.migrate()
    from app.main import app

    return TestClient(app)


def test_saas_status(saas_client):
    r = saas_client.get("/saas/status")
    assert r.status_code == 200
    assert r.json()["saas_mode"] is True


def test_saas_root_public_redirects_to_landing(saas_client):
    r = saas_client.get("/", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == "/ui/saas"
    landing = saas_client.get("/ui/saas")
    assert landing.status_code == 200
    assert b"Sign in" in landing.content or b"sign" in landing.content.lower()


def test_admin_create_tenant_and_issue_key(saas_client):
    r = saas_client.post(
        "/ui/saas/login",
        data={"api_token": "admin-secret"},
        follow_redirects=False,
    )
    assert r.status_code == 303

    r = saas_client.post(
        "/ui/saas/app/admin/tenants",
        data={
            "tenant_id": "acme",
            "name": "Acme Photography",
            "store_slug": "acme-photo",
            "monthly_recommend_cap": "100",
        },
        follow_redirects=False,
    )
    assert r.status_code == 200
    assert b"plutus_tk_acme_" in r.content

    issued = tenants.issue_api_key("acme", label="test")
    assert issued["api_key"].startswith("plutus_tk_acme_")

    tenant = db.get_tenant("acme")
    assert tenant is not None
    assert tenant["store_slug"] == "acme-photo"


def test_admin_patch_rejects_duplicate_store_slug(saas_client):
    from app import tenants

    tenants.create_tenant("alpha", name="Alpha", store_slug="alpha-shop")
    tenants.create_tenant("beta", name="Beta", store_slug="beta-shop")

    r = saas_client.post(
        "/ui/saas/login",
        data={"api_token": "admin-secret"},
        follow_redirects=False,
    )
    assert r.status_code == 303

    r = saas_client.post(
        "/ui/saas/app/admin/tenants/beta",
        data={"store_slug": "alpha-shop", "active": "1"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert "store+slug+already+taken" in r.headers["location"]

    beta = db.get_tenant("beta")
    assert beta["store_slug"] == "beta-shop"


def test_tenant_scoped_run_and_storefront(saas_client, tmp_path, monkeypatch):
    tenants.create_tenant("studio", name="Studio One", store_slug="studio-one")
    issued = tenants.issue_api_key("studio")
    api_key = issued["api_key"]

    folder = tmp_path / "gallery"
    folder.mkdir()
    Image.new("RGB", (80, 60), color=(120, 90, 40)).save(folder / "01.jpg")
    monkeypatch.setattr(config, "MISE_MEDIA_ROOT", None)

    r = saas_client.post(
        "/ui/saas/login",
        data={"api_token": api_key},
        follow_redirects=False,
    )
    assert r.status_code == 303

    from app import service

    result = service.analyze_folder(folder, name="Test Gallery", tenant_id="studio")

    run_id = result["run_id"]
    scoped = db.get_run(run_id, tenant_id="studio")
    assert scoped is not None

    other = db.get_run(run_id, tenant_id="other")
    assert other is None

    from app.storefront import create_share_link

    link = create_share_link(tenant_id="studio", run_id=run_id, label="Client offer")
    r = saas_client.get(link["url"])
    assert r.status_code == 200
    assert b"Buy this package" in r.content or b"checkout" in r.content.lower()


def test_protected_runs_require_auth(saas_client):
    r = saas_client.get("/runs/1")
    assert r.status_code == 401