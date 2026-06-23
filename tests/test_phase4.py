from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app import config, db, lab, metering, signup


@pytest.fixture()
def saas_client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(config, "SAAS_MODE", True)
    monkeypatch.setattr(config, "SIGNUP_ENABLED", True)
    monkeypatch.setattr(config, "API_TOKEN", "admin-secret")
    monkeypatch.setattr(config, "TENANT_KEY_PEPPER", "pepper-secret")
    monkeypatch.setattr(config, "RATE_LIMIT_ENABLED", False)
    monkeypatch.setattr(config, "UPLOAD_ASYNC_ANALYZE", False)
    monkeypatch.setattr(config, "LAB_ADAPTER", "mock")
    monkeypatch.setattr(config, "LAB_MOCK_PROCESS_SECONDS", 0)
    monkeypatch.setattr(config, "LAB_MOCK_SHIP_SECONDS", 0)
    db.migrate()
    from app.main import app

    return TestClient(app)


def test_self_signup_creates_trial_tenant(saas_client):
    r = saas_client.post(
        "/ui/saas/signup",
        data={
            "studio_name": "North Light Studio",
            "email": "hello@northlight.test",
            "store_slug": "north-light",
        },
    )
    assert r.status_code == 200
    assert b"plutus_tk_north-light_" in r.content

    tenant = db.get_tenant("north-light")
    assert tenant is not None
    assert tenant["notify_email"] == "hello@northlight.test"
    assert tenant["billing_status"] == "trialing"
    assert tenant["plan_tier"] == "trial"


def test_signup_disabled_redirects(saas_client, monkeypatch):
    monkeypatch.setattr(config, "SIGNUP_ENABLED", False)
    r = saas_client.get("/ui/saas/signup", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == "/ui/saas/login"


def test_lab_poll_advances_mock_status(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(config, "LAB_ADAPTER", "mock")
    monkeypatch.setattr(config, "LAB_MOCK_PROCESS_SECONDS", 0)
    monkeypatch.setattr(config, "LAB_MOCK_SHIP_SECONDS", 0)
    db.migrate()

    from app import tenants

    tenants.create_tenant("t1", name="T1", store_slug="t1")
    gid = db.insert_gallery(name="g", source="/x", photo_count=1, tenant_id="t1")
    rid = db.insert_run(
        gallery_id=gid,
        engine="mock",
        bundle_count=1,
        estimated_total_cents=1000,
        payload={"bundles": []},
        tenant_id="t1",
    )
    oid = db.create_order(
        tenant_id="t1",
        run_id=rid,
        bundle_index=0,
        total_cents=4500,
        items=[{"sku": "p", "label": "P", "quantity": 1, "unit_cents": 4500}],
    )
    paid_at = (datetime.now(UTC) - timedelta(minutes=30)).isoformat()
    db.update_order(oid, status="paid", paid_at=paid_at)
    lab.submit_order(oid)

    result = lab.poll_order(oid)
    assert result["advanced"] is True
    assert result["lab_status"] == "processing"

    events = db.list_fulfillment_events(oid)
    assert len(events) >= 2


def test_trial_expiry_blocks_recommend(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(config, "SIGNUP_TRIAL_DAYS", 7)
    db.migrate()

    from app import tenants

    tenants.create_tenant("old", name="Old", store_slug="old")
    with db.connection() as con:
        con.execute(
            "UPDATE tenants SET created_at=?, billing_status='trialing' WHERE id='old'",
            ((datetime.now(UTC) - timedelta(days=30)).isoformat(),),
        )
    with pytest.raises(metering.MeteringError, match="trial expired"):
        metering.check_recommend_cap("old")


def test_register_studio_validation(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(config, "SAAS_MODE", True)
    monkeypatch.setattr(config, "SIGNUP_ENABLED", True)
    db.migrate()

    with pytest.raises(signup.SignupError):
        signup.register_studio(studio_name="x", email="bad-email")