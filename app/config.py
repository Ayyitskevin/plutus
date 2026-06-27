"""Environment-backed settings for the Plutus offers worker (single operator)."""
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

# Argus vision enrichment (optional keeper/hero signals).
ARGUS_URL = os.environ.get("PLUTUS_ARGUS_URL", "").rstrip("/")
ARGUS_TOKEN = os.environ.get("PLUTUS_ARGUS_TOKEN", "")
ARGUS_TIMEOUT = int(os.environ.get("PLUTUS_ARGUS_TIMEOUT", "600"))
ARGUS_AUTO_VISION = os.environ.get("PLUTUS_ARGUS_AUTO_VISION", "true").lower() == "true"
ARGUS_ANALYZE_LIMIT = int(os.environ.get("PLUTUS_ARGUS_ANALYZE_LIMIT", "0"))

# Inbound service-token register (constant-time). Mise authenticates the recommend
# path with PLUTUS_API_TOKEN (== Mise's MISE_PLUTUS_TOKEN); PLUTUS_MISE_HOOK_TOKEN
# and PLUTUS_SERVICE_TOKENS are additional accepted tokens so a secret rotation
# never strands the publish path with a hard 401.
API_TOKEN = os.environ.get("PLUTUS_API_TOKEN", "")
MISE_HOOK_TOKEN = os.environ.get("PLUTUS_MISE_HOOK_TOKEN") or None
SERVICE_TOKENS = [
    t.strip() for t in os.environ.get("PLUTUS_SERVICE_TOKENS", "").split(",") if t.strip()
]

# Mise gallery index (read API) + originals path.
MISE_URL = os.environ.get("PLUTUS_MISE_URL", "").rstrip("/")
MISE_API_TOKEN = os.environ.get("PLUTUS_MISE_API_TOKEN", "")
MISE_TIMEOUT = int(os.environ.get("PLUTUS_MISE_TIMEOUT", "10"))
MISE_MEDIA_ROOT = (
    Path(os.environ.get("PLUTUS_MISE_MEDIA_ROOT", ""))
    if os.environ.get("PLUTUS_MISE_MEDIA_ROOT")
    else None
)

# Optional async push of the finished offer back to Mise. Default OFF — the
# synchronous /recommend/mise-gallery response stays the live contract. When
# enabled, results POST to {MISE_CALLBACK_URL or MISE_URL}/api/plutus/callback
# ?gallery_id=<id> with a bearer service token ({MISE_CALLBACK_TOKEN or
# MISE_API_TOKEN}); failures are swallowed and recorded, never crashing recommend.
MISE_CALLBACK_ENABLED = (
    os.environ.get("PLUTUS_MISE_CALLBACK_ENABLED", "false").lower() == "true"
)
MISE_CALLBACK_URL = os.environ.get("PLUTUS_MISE_CALLBACK_URL", "").rstrip("/") or None
MISE_CALLBACK_TOKEN = os.environ.get("PLUTUS_MISE_CALLBACK_TOKEN") or None
# Delivery hardening: transient failures retry with exponential backoff up to
# MAX_ATTEMPTS (base * 2**n seconds), then the offer is dead-lettered locally
# (re-deliverable) rather than lost. A 401 triggers a one-shot token refresh+retry.
MISE_CALLBACK_MAX_ATTEMPTS = int(os.environ.get("PLUTUS_MISE_CALLBACK_MAX_ATTEMPTS", "3"))
MISE_CALLBACK_BACKOFF_BASE = float(os.environ.get("PLUTUS_MISE_CALLBACK_BACKOFF_BASE", "0.5"))

PHOTO_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".tif", ".tiff"}

# Base URL for the review/pitch links returned to Mise admin.
PUBLIC_URL = os.environ.get("PLUTUS_PUBLIC_URL", f"http://{HOST}:{PORT}").rstrip("/")
# Optional — "back to Mise gallery" link on Plutus run review pages.
MISE_ADMIN_URL = os.environ.get("PLUTUS_MISE_ADMIN_URL", "").rstrip("/")

# Dionysus pitch enrichment (optional).
DIONYSUS_URL = os.environ.get("PLUTUS_DIONYSUS_URL", "").rstrip("/")
DIONYSUS_TOKEN = os.environ.get("PLUTUS_DIONYSUS_TOKEN") or None
DIONYSUS_ORG_SLUG = os.environ.get("PLUTUS_DIONYSUS_ORG_SLUG") or None
DIONYSUS_TIMEOUT = int(os.environ.get("PLUTUS_DIONYSUS_TIMEOUT", "10"))

# Ops.
PROMETHEUS_ENABLED = os.environ.get("PLUTUS_PROMETHEUS_ENABLED", "false").lower() == "true"
CORS_ORIGINS = [
    origin.strip()
    for origin in os.environ.get("PLUTUS_CORS_ORIGINS", "").split(",")
    if origin.strip()
]

LOG_LEVEL = os.environ.get("PLUTUS_LOG_LEVEL", "INFO").upper()
logging.basicConfig(level=LOG_LEVEL, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
