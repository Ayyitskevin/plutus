"""Environment-backed settings."""
from __future__ import annotations

import logging
import os
from pathlib import Path

from dotenv import load_dotenv

_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_ROOT / ".env", override=False)

DATA_DIR = Path(os.environ.get("PLUTUS_DATA_DIR", _ROOT / "data"))
DB_PATH = DATA_DIR / "plutus.db"
DATABASE_URL = os.environ.get("PLUTUS_DATABASE_URL") or None
DB_BACKEND = "postgres" if DATABASE_URL else "sqlite"

HOST = os.environ.get("PLUTUS_HOST", "0.0.0.0")
PORT = int(os.environ.get("PLUTUS_PORT", "8030"))

ARGUS_URL = os.environ.get("PLUTUS_ARGUS_URL", "").rstrip("/")
ARGUS_TOKEN = os.environ.get("PLUTUS_ARGUS_TOKEN", "")
ARGUS_TIMEOUT = int(os.environ.get("PLUTUS_ARGUS_TIMEOUT", "600"))
ARGUS_AUTO_VISION = os.environ.get("PLUTUS_ARGUS_AUTO_VISION", "true").lower() == "true"
ARGUS_ANALYZE_LIMIT = int(os.environ.get("PLUTUS_ARGUS_ANALYZE_LIMIT", "10"))

API_TOKEN = os.environ.get("PLUTUS_API_TOKEN", "")

MISE_URL = os.environ.get("PLUTUS_MISE_URL", "").rstrip("/")
MISE_API_TOKEN = os.environ.get("PLUTUS_MISE_API_TOKEN", "")
MISE_TIMEOUT = int(os.environ.get("PLUTUS_MISE_TIMEOUT", "10"))
MISE_MEDIA_ROOT = (
    Path(os.environ.get("PLUTUS_MISE_MEDIA_ROOT", ""))
    if os.environ.get("PLUTUS_MISE_MEDIA_ROOT")
    else None
)

PHOTO_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".tif", ".tiff"}

# SaaS mode (off by default — homelab single-tenant)
SAAS_MODE = os.environ.get("PLUTUS_SAAS_MODE", "false").lower() == "true"

# Homelab client storefront (share links + checkout on :8030)
HOMELAB_STORE_ENABLED = os.environ.get("PLUTUS_HOMELAB_STORE_ENABLED", "true").lower() == "true"
HOMELAB_TENANT_ID = os.environ.get("PLUTUS_HOMELAB_TENANT_ID", "homelab")
HOMELAB_STORE_SLUG = os.environ.get("PLUTUS_HOMELAB_STORE_SLUG", "studio")
HOMELAB_STUDIO_NAME = os.environ.get("PLUTUS_HOMELAB_STUDIO_NAME", "Kevin Lee Studio")
TENANT_KEY_PEPPER = os.environ.get("PLUTUS_TENANT_KEY_PEPPER") or API_TOKEN or "plutus-dev-pepper"
SAAS_PUBLIC_URL = os.environ.get("PLUTUS_SAAS_PUBLIC_URL", f"http://{HOST}:{PORT}")

# Self-service signup (on when SaaS mode unless explicitly disabled)
SIGNUP_ENABLED = os.environ.get("PLUTUS_SIGNUP_ENABLED", "true").lower() == "true"
SIGNUP_TRIAL_DAYS = int(os.environ.get("PLUTUS_SIGNUP_TRIAL_DAYS", "14"))
SIGNUP_TRIAL_RECOMMEND_CAP = int(os.environ.get("PLUTUS_SIGNUP_TRIAL_RECOMMEND_CAP", "25"))
SIGNUP_REDIRECT_BILLING = (
    os.environ.get("PLUTUS_SIGNUP_REDIRECT_BILLING", "false").lower() == "true"
)
SIGNUP_VERIFY_EMAIL = os.environ.get("PLUTUS_SIGNUP_VERIFY_EMAIL", "true").lower() == "true"
SIGNUP_VERIFY_TOKEN_HOURS = int(os.environ.get("PLUTUS_SIGNUP_VERIFY_TOKEN_HOURS", "48"))
SIGNUP_VERIFY_DEV_BYPASS = (
    os.environ.get("PLUTUS_SIGNUP_VERIFY_DEV_BYPASS", "false").lower() == "true"
)

# Mise publish hook — default SaaS tenant when flow POSTs without tenant_id
MISE_HOOK_TENANT_ID = os.environ.get("PLUTUS_MISE_HOOK_TENANT_ID") or None
MISE_HOOK_TOKEN = os.environ.get("PLUTUS_MISE_HOOK_TOKEN") or None

# Production hardening
SAAS_DISABLE_OPENAPI = os.environ.get("PLUTUS_SAAS_DISABLE_OPENAPI", "true").lower() == "true"
UPLOAD_ANALYZE_STALE_MINUTES = int(os.environ.get("PLUTUS_UPLOAD_ANALYZE_STALE_MINUTES", "15"))
SHUTDOWN_GRACE_SECONDS = int(os.environ.get("PLUTUS_SHUTDOWN_GRACE_SECONDS", "30"))

# Ops
RATE_LIMIT_ENABLED = os.environ.get("PLUTUS_RATE_LIMIT_ENABLED", "true").lower() == "true"
RATE_LIMIT_PER_MINUTE = int(os.environ.get("PLUTUS_RATE_LIMIT_PER_MINUTE", "60"))
RATE_LIMIT_RECOMMEND_PER_MINUTE = int(
    os.environ.get("PLUTUS_RATE_LIMIT_RECOMMEND_PER_MINUTE", "20")
)
# Which proxy header (if any) to trust for the client IP behind a reverse proxy.
# "" (default) trusts only the socket peer — forwarded headers are spoofable, so a
# client could otherwise rotate X-Forwarded-For to dodge per-IP limits. Set to
# "cloudflare" behind the CF tunnel (uses CF-Connecting-IP) or "xff" only when a
# trusted proxy you control rewrites X-Forwarded-For.
RATE_LIMIT_TRUSTED_PROXY = os.environ.get("PLUTUS_RATE_LIMIT_TRUSTED_PROXY", "").strip().lower()
REDIS_URL = os.environ.get("PLUTUS_REDIS_URL") or None

AUDIT_LOG_ENABLED = os.environ.get("PLUTUS_AUDIT_LOG_ENABLED", "true").lower() == "true"
AUDIT_LOG_RETENTION_DAYS = int(os.environ.get("PLUTUS_AUDIT_LOG_RETENTION_DAYS", "90"))
STRUCTURED_LOGS = os.environ.get("PLUTUS_STRUCTURED_LOGS", "true").lower() == "true"
PROMETHEUS_ENABLED = os.environ.get("PLUTUS_PROMETHEUS_ENABLED", "false").lower() == "true"

CORS_ORIGINS = [
    origin.strip()
    for origin in os.environ.get("PLUTUS_CORS_ORIGINS", "").split(",")
    if origin.strip()
]

# Stripe — tenant subscriptions + client bundle checkout
STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY") or None
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET") or None
STRIPE_PRICE_ID = os.environ.get("STRIPE_PRICE_ID") or None
STRIPE_SUCCESS_URL = os.environ.get(
    "STRIPE_SUCCESS_URL",
    f"{SAAS_PUBLIC_URL.rstrip('/')}/ui/saas/billing?success=1",
)
STRIPE_CANCEL_URL = os.environ.get(
    "STRIPE_CANCEL_URL",
    f"{SAAS_PUBLIC_URL.rstrip('/')}/ui/saas/billing?cancelled=1",
)
STRIPE_BILLING_PORTAL_RETURN_URL = os.environ.get(
    "STRIPE_BILLING_PORTAL_RETURN_URL",
    f"{SAAS_PUBLIC_URL.rstrip('/')}/ui/saas/billing",
)
STRIPE_STORE_SUCCESS_URL = os.environ.get(
    "STRIPE_STORE_SUCCESS_URL",
    f"{SAAS_PUBLIC_URL.rstrip('/')}/store/order/success",
)
STRIPE_STORE_CANCEL_URL = os.environ.get(
    "STRIPE_STORE_CANCEL_URL",
    f"{SAAS_PUBLIC_URL.rstrip('/')}/store/order/cancelled",
)

# Tenant gallery storage (local | s3)
STORAGE_BACKEND = os.environ.get("PLUTUS_STORAGE_BACKEND", "local").lower()
S3_BUCKET = os.environ.get("PLUTUS_S3_BUCKET") or None
S3_REGION = os.environ.get("PLUTUS_S3_REGION", "us-east-1")
S3_ENDPOINT = os.environ.get("PLUTUS_S3_ENDPOINT") or None
S3_ACCESS_KEY = os.environ.get("PLUTUS_S3_ACCESS_KEY") or None
S3_SECRET_KEY = os.environ.get("PLUTUS_S3_SECRET_KEY") or None
S3_PREFIX = os.environ.get("PLUTUS_S3_PREFIX", "plutus/tenants")

MAX_UPLOAD_FILE_BYTES = int(os.environ.get("PLUTUS_MAX_UPLOAD_FILE_BYTES", str(25 * 1024 * 1024)))
MAX_UPLOAD_FILES = int(os.environ.get("PLUTUS_MAX_UPLOAD_FILES", "50"))

# Lab fulfillment (mock | disabled | whcc)
LAB_ADAPTER = os.environ.get("PLUTUS_LAB_ADAPTER", "mock").lower()
LAB_MOCK_PROCESS_SECONDS = int(os.environ.get("PLUTUS_LAB_MOCK_PROCESS_SECONDS", "120"))
LAB_MOCK_SHIP_SECONDS = int(os.environ.get("PLUTUS_LAB_MOCK_SHIP_SECONDS", "600"))

WHCC_API_URL = os.environ.get("WHCC_API_URL", "").rstrip("/")
WHCC_API_KEY = os.environ.get("WHCC_API_KEY") or None
WHCC_ACCOUNT_ID = os.environ.get("WHCC_ACCOUNT_ID") or None
WHCC_WEBHOOK_SECRET = os.environ.get("WHCC_WEBHOOK_SECRET") or None
WHCC_RETRY_ATTEMPTS = int(os.environ.get("WHCC_RETRY_ATTEMPTS", "3"))

# Dionysus pitch enrichment (optional)
DIONYSUS_URL = os.environ.get("PLUTUS_DIONYSUS_URL", "").rstrip("/")
DIONYSUS_TOKEN = os.environ.get("PLUTUS_DIONYSUS_TOKEN") or None
DIONYSUS_ORG_SLUG = os.environ.get("PLUTUS_DIONYSUS_ORG_SLUG") or None
DIONYSUS_TIMEOUT = int(os.environ.get("PLUTUS_DIONYSUS_TIMEOUT", "10"))

# Order notifications
ORDER_ALERT_EMAIL = os.environ.get("PLUTUS_ORDER_ALERT_EMAIL") or None
ORDER_WEBHOOK_URL = os.environ.get("PLUTUS_ORDER_WEBHOOK_URL") or None
NOTIFY_LAB_SHIPPED = os.environ.get("PLUTUS_NOTIFY_LAB_SHIPPED", "true").lower() == "true"
NOTIFY_CLIENT_ON_PAID = os.environ.get("PLUTUS_NOTIFY_CLIENT_ON_PAID", "true").lower() == "true"

# SaaS publish wizard — max wait for async upload analyze
SELL_ANALYZE_TIMEOUT = int(os.environ.get("PLUTUS_SELL_ANALYZE_TIMEOUT", "600"))
SMTP_HOST = os.environ.get("PLUTUS_SMTP_HOST") or None
SMTP_PORT = int(os.environ.get("PLUTUS_SMTP_PORT", "587"))
SMTP_USER = os.environ.get("PLUTUS_SMTP_USER") or None
SMTP_PASSWORD = os.environ.get("PLUTUS_SMTP_PASSWORD") or None
SMTP_FROM = os.environ.get("PLUTUS_SMTP_FROM") or SMTP_USER

# Dev/test — simulate client checkout completion without Stripe card (test keys only)
ALLOW_SIMULATE_PAYMENT = (
    os.environ.get("PLUTUS_ALLOW_SIMULATE_PAYMENT", "false").lower() == "true"
)

# Async upload analyze (queue Argus vision + recommend off the HTTP request)
UPLOAD_ASYNC_ANALYZE = os.environ.get("PLUTUS_UPLOAD_ASYNC_ANALYZE", "true").lower() == "true"
UPLOAD_WORKER_INTERVAL = int(os.environ.get("PLUTUS_UPLOAD_WORKER_INTERVAL", "2"))

LOG_LEVEL = os.environ.get("PLUTUS_LOG_LEVEL", "INFO").upper()
logging.basicConfig(level=LOG_LEVEL, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")