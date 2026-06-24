#!/usr/bin/env python3
"""One-shot script: split app/main.py into app/routes/*.py modules."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MAIN = ROOT / "app" / "main.py"
ROUTES = ROOT / "app" / "routes"

CSRF_POSTS = {
    "/ui/logout",
    "/ui/saas/app/settings",
    "/ui/saas/app/notifications/test",
    "/ui/saas/app/orders/{order_id}/poll-lab",
    "/ui/saas/app/orders/{order_id}/resend-confirmation",
    "/ui/saas/app/admin/tenants",
    "/ui/saas/app/admin/tenants/{tenant_id}",
    "/ui/saas/app/admin/tenants/{tenant_id}/keys",
    "/ui/saas/app/admin/tenants/{tenant_id}/keys/{key_id}/revoke",
    "/ui/saas/app/admin/tenants/{tenant_id}/billing/checkout",
    "/ui/saas/app/sell",
    "/ui/saas/app/upload",
    "/ui/saas/app/upload/{batch_id}/analyze",
    "/ui/saas/app/mise/{gallery_id}/recommend",
    "/ui/saas/app/catalog",
    "/ui/saas/app/keys",
    "/ui/saas/app/keys/{key_id}/revoke",
    "/ui/saas/app/share-link",
    "/ui/saas/billing/checkout",
    "/ui/saas/billing/portal",
}

MODULE_MAP = [
    ("health", r"^/(healthz|saas/status|saas/billing/status|metrics)$"),
    ("api", r"^/(recommend/|upload-batches/|analyze-folder|api/mise/)"),
    ("webhooks", r"^/webhooks/"),
    ("storefront", r"^/(store/|storefront/|orders/)"),
    ("homelab_ui", r"^/$|^/analyze$|^/runs/"),
    ("saas_public", r"^/ui/(saas$|saas/login|saas/signup|saas/verify|saas/signup/pending|saas/resend)"),
    ("saas_billing", r"^/ui/saas/billing"),
    ("saas_app", r"^/ui/saas/app"),
    ("homelab_ui", r"^/ui/homelab/"),
]

COMMON_IMPORTS = """from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote_plus

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse

from .. import (
    audit,
    billing,
    catalog,
    config,
    db,
    health,
    homelab,
    lab,
    metrics,
    order_tracking,
    pitch,
    saas,
    sell,
    service,
    signup,
    storage,
    ui_sessions,
    uploads,
)
from ..auth import require_bearer, resolve_auth
from ..auth_context import AuthContext
from ..metering import MeteringError
from ..orders import OrderError, create_bundle_checkout, simulate_test_payment
from ..sell import SellError
from ..storefront import StorefrontError, create_share_link, resolve_offer
from ..tenants import TenantError
from .deps import (
    admin_tenant_context,
    admin_ui_redirect,
    error,
    request_auth,
    templates,
    tenant_ui_redirect,
    ui_context,
    ui_saas_auth,
)

log = logging.getLogger("plutus")
"""

CSRF_EXTRA = ""


def route_meta(decorator_line: str) -> tuple[str, str] | None:
    m = re.search(r'@app\.(get|post)\("([^"]+)"', decorator_line)
    return (m.group(2), m.group(1).upper()) if m else None


def classify(path: str, method: str) -> str:
    if method == "POST" and path in CSRF_POSTS:
        return "saas_mutations"
    for name, pattern in MODULE_MAP:
        if re.search(pattern, path):
            return name
    raise ValueError(f"unclassified route: {path}")


def transform_block(block: str, module: str) -> str:
    block = block.replace("@app.", "@router.")
    block = block.replace("_ui_context(", "ui_context(")
    block = block.replace("_request_auth(", "request_auth(")
    block = block.replace("_ui_saas_auth(", "ui_saas_auth(")
    block = block.replace("_tenant_ui_redirect(", "tenant_ui_redirect(")
    block = block.replace("_admin_ui_redirect(", "admin_ui_redirect(")
    block = block.replace("_admin_tenant_context(", "admin_tenant_context(")
    block = block.replace("from . import ", "from .. import ")
    block = block.replace("from .metering", "from ..metering")
    if module == "saas_mutations":
        block = re.sub(
            r"def ui_logout\(request: Request, csrf_token: str = Form\(\"\"\)\):\n"
            r"    verify_ui_csrf\(request, csrf_token\)\n",
            "def ui_logout(request: Request):\n",
            block,
        )
    return block


def extract_route_blocks(lines: list[str]) -> list[tuple[str, str, list[str]]]:
    """Return [(path, method, block_lines), ...] for each @app route handler."""
    start = next(i for i, ln in enumerate(lines) if ln.startswith("# --- Health"))
    end = next(i for i, ln in enumerate(lines) if ln.startswith("def main()"))
    chunk = lines[start:end]

    blocks: list[tuple[str, str, list[str]]] = []
    current: list[str] = []
    current_path: str | None = None
    current_method: str | None = None
    in_helper = False
    helper_depth = 0

    for ln in chunk:
        if ln.startswith("# ---"):
            continue
        if ln.startswith("def _tenant_ui_redirect") or ln.startswith("def _admin_ui_redirect") or ln.startswith("def _admin_tenant_context"):
            if current and current_path and current_method:
                blocks.append((current_path, current_method, current))
                current = []
                current_path = None
                current_method = None
            in_helper = True
            continue
        if in_helper:
            if ln and not ln[0].isspace():
                in_helper = False
            elif ln.startswith("@app."):
                in_helper = False
            else:
                continue
        if ln.startswith("@app."):
            if current and current_path and current_method:
                blocks.append((current_path, current_method, current))
            current = [ln]
            meta = route_meta(ln)
            if meta:
                current_path, current_method = meta
            continue
        if current:
            current.append(ln)

    if current and current_path and current_method:
        blocks.append((current_path, current_method, current))
    return blocks


def write_module(name: str, handlers: list[tuple[str, str, list[str]]]) -> None:
    body = "\n\n".join(transform_block("\n".join(h), name) for _, _, h in handlers)
    if name == "saas_mutations":
        router_def = "router = APIRouter(dependencies=[Depends(require_csrf)])\n\n"
        imports = COMMON_IMPORTS.replace(
            "from .deps import (",
            "from .csrf import require_csrf\nfrom .deps import (",
        )
    else:
        router_def = "router = APIRouter()\n\n"
        imports = COMMON_IMPORTS
    content = imports + router_def + body + "\n"
    (ROUTES / f"{name}.py").write_text(content)


def write_init(modules: list[str]) -> None:
    includes = "\n".join(
        f"    from .{m} import router as {m}_router\n"
        f"    app.include_router({m}_router)"
        for m in modules
    )
    text = f'''"""Register Plutus HTTP route modules."""
from __future__ import annotations

from fastapi import FastAPI


def register_routes(app: FastAPI) -> None:
{includes}
'''
    (ROUTES / "__init__.py").write_text(text)


def write_slim_main(original: str) -> str:
    lines = original.splitlines()
    cut = next(i for i, ln in enumerate(lines) if ln.startswith("# --- Health"))
    preamble = lines[:cut]

    # Drop moved helpers from preamble
    drop_prefixes = (
        "templates = ",
        "def _fmt_cents",
        "templates.env.filters",
        "def _ui_context",
        "def _request_auth",
        "def _ui_saas_auth",
        "def error(",
    )
    cleaned: list[str] = []
    skip_block = False
    for ln in preamble:
        if any(ln.strip().startswith(p) for p in drop_prefixes):
            skip_block = True
            continue
        if skip_block:
            if ln and not ln[0].isspace():
                skip_block = False
            else:
                continue
        cleaned.append(ln)

    # Normalize imports
    out: list[str] = []
    for ln in cleaned:
        if ln == "from .auth import UI_TOKEN_COOKIE, require_bearer, resolve_auth, verify_ui_csrf":
            continue
        if ln.startswith("from fastapi import Depends, FastAPI"):
            out.append("from fastapi import FastAPI")
            continue
        if ln.startswith("from fastapi.responses import"):
            continue
        if ln.startswith("from fastapi.templating import"):
            continue
        if ln.startswith("from datetime import"):
            continue
        if ln.startswith("from urllib.parse import"):
            continue
        if ln.startswith("import json"):
            continue
        if ln.startswith("from .auth_context import"):
            continue
        if ln.startswith("from .metering import"):
            continue
        if ln.startswith("from .orders import"):
            continue
        if ln.startswith("from .sell import"):
            continue
        if ln.startswith("from .storefront import"):
            continue
        if ln.startswith("from .tenants import"):
            continue
        out.append(ln)

    if not any("register_routes" in ln for ln in out):
        out.insert(
            next(i for i, ln in enumerate(out) if ln.startswith("from . import")),
            "from .routes import register_routes",
        )

    tail = '''

register_routes(app)


def main() -> None:
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=config.HOST,
        port=config.PORT,
        reload=False,
    )


if __name__ == "__main__":
    main()
'''
    return "\n".join(out) + tail


def main() -> None:
    source = MAIN.read_text()
    lines = source.splitlines()
    route_blocks = extract_route_blocks(lines)

    grouped: dict[str, list[tuple[str, str, list[str]]]] = {}
    for path, method, block in route_blocks:
        mod = classify(path, method)
        grouped.setdefault(mod, []).append((path, method, block))

    order = [
        "health",
        "api",
        "webhooks",
        "storefront",
        "homelab_ui",
        "saas_public",
        "saas_app",
        "saas_mutations",
        "saas_billing",
    ]
    for name in order:
        if name in grouped:
            write_module(name, grouped[name])
    write_init([n for n in order if n in grouped])
    MAIN.write_text(write_slim_main(source))
    paths = [f"{m} {p}" for p, m, _ in route_blocks]
    print(f"Split {len(paths)} routes into: {', '.join(grouped.keys())}")


if __name__ == "__main__":
    main()