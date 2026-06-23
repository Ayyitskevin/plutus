"""Environment-backed settings."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_ROOT / ".env", override=False)

DATA_DIR = Path(os.environ.get("PLUTUS_DATA_DIR", _ROOT / "data"))
DB_PATH = DATA_DIR / "plutus.db"

HOST = os.environ.get("PLUTUS_HOST", "0.0.0.0")
PORT = int(os.environ.get("PLUTUS_PORT", "8030"))

ARGUS_URL = os.environ.get("PLUTUS_ARGUS_URL", "").rstrip("/")
ARGUS_TOKEN = os.environ.get("PLUTUS_ARGUS_TOKEN", "")

PHOTO_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".tif", ".tiff"}