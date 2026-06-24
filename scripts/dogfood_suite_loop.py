#!/usr/bin/env python3
"""Suite integration loop: Mise → Argus → Plutus offer → Mnemosyne share CTA.

Usage:
  python scripts/dogfood_suite_loop.py
  python scripts/dogfood_suite_loop.py --gallery-id 1 --mnemosyne-album-id 3

Env (from plutus .env or shell):
  ARGUS_API_TOKEN, ARGUS_HOST, ARGUS_PORT
  PLUTUS_SAAS_URL (default http://127.0.0.1:8031) — used for offer mint verify
  MNEMOSYNE_URL, MNEMOSYNE_DOGFOOD_EMAIL, MNEMOSYNE_DOGFOOD_PASSWORD
  MNEMOSYNE_ALBUM_ID — ready album for plutus-generate (optional)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from http.cookiejar import CookieJar
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_env_file(path: Path) -> None:
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        os.environ.setdefault(key.strip(), val.strip())


def _load_dotenv() -> None:
    _load_env_file(ROOT / ".env")
    _load_env_file(ROOT.parent / "argus" / ".env")


def _get(url: str, *, headers: dict | None = None, timeout: float = 15.0) -> tuple[int, str]:
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode(errors="replace")


def _no_redirect_opener() -> urllib.request.OpenerDirector:
    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None

    return urllib.request.build_opener(_NoRedirect())


def _post_form(
    url: str,
    fields: dict[str, str],
    *,
    headers: dict | None = None,
    opener: urllib.request.OpenerDirector | None = None,
    timeout: float = 120.0,
    allow_redirects: bool = True,
) -> tuple[int, str, dict[str, str]]:
    body = urllib.parse.urlencode(fields).encode()
    hdrs = {"Content-Type": "application/x-www-form-urlencoded", **(headers or {})}
    req = urllib.request.Request(url, data=body, method="POST", headers=hdrs)
    open_fn = (
        opener.open
        if opener
        else (
            _no_redirect_opener().open
            if not allow_redirects
            else urllib.request.urlopen
        )
    )
    try:
        with open_fn(req, timeout=timeout) as resp:
            loc = resp.headers.get("Location", "")
            return resp.status, resp.read().decode(errors="replace"), {"location": loc}
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode(errors="replace"), {
            "location": exc.headers.get("Location", "")
        }


def _argus_base() -> str:
    host = os.environ.get("ARGUS_HOST", "127.0.0.1")
    port = os.environ.get("ARGUS_PORT", "8010")
    return f"http://{host}:{port}"


def _plutus_saas_base() -> str:
    return os.environ.get("PLUTUS_SAAS_URL", "http://127.0.0.1:8031").rstrip("/")


def _mnemosyne_base() -> str:
    return os.environ.get("MNEMOSYNE_URL", "http://127.0.0.1:8000").rstrip("/")


def _token() -> str:
    return (
        os.environ.get("ARGUS_API_TOKEN")
        or os.environ.get("PLUTUS_ARGUS_TOKEN")
        or os.environ.get("PLUTUS_API_TOKEN", "")
    )


def _tenant_id() -> str:
    return (
        os.environ.get("MNEMOSYNE_PLUTUS_TENANT_ID")
        or os.environ.get("PLUTUS_MISE_HOOK_TENANT_ID")
        or "flow-studio"
    )


def run_plutus_mise_hook(gallery_id: int) -> dict:
    """Direct Plutus SaaS path — skips Argus when homelab Argus points at :8030."""
    token = os.environ.get("PLUTUS_MISE_HOOK_TOKEN", "")
    if not token:
        raise RuntimeError("PLUTUS_MISE_HOOK_TOKEN required")
    code, body, _ = _post_form(
        f"{_plutus_saas_base()}/webhooks/mise/gallery-published",
        {"mise_gallery_id": str(gallery_id)},
        headers={"Authorization": f"Bearer {token}"},
        timeout=120.0,
    )
    if code != 200:
        raise RuntimeError(f"plutus hook HTTP {code}: {body[:240]}")
    payload = json.loads(body)
    run_id = payload.get("run_id")
    offer = payload.get("offer_url") or ""
    bundles = payload.get("bundles") or []
    return {
        "offer_url": offer,
        "message": f"upsell run {run_id} ({len(bundles)} bundles)",
        "plutus_run_id": run_id,
    }


def run_argus_pipeline(gallery_id: int) -> dict:
    token = _token()
    if not token:
        raise RuntimeError("ARGUS_API_TOKEN or PLUTUS_API_TOKEN required")
    code, _, meta = _post_form(
        f"{_argus_base()}/ui/pipeline/run-all/{gallery_id}",
        {"api_token": token},
        timeout=600.0,
        allow_redirects=False,
    )
    loc = meta.get("location") or ""
    if code != 303:
        raise RuntimeError(f"run-all HTTP {code}")
    qs = urllib.parse.parse_qs(urllib.parse.urlparse(loc).query)
    if qs.get("error"):
        raise RuntimeError(urllib.parse.unquote_plus(qs["error"][0]))
    offer = urllib.parse.unquote_plus((qs.get("offer_url") or [""])[0])
    msg = urllib.parse.unquote_plus((qs.get("msg") or [""])[0])
    if not offer:
        raise RuntimeError(f"pipeline returned no offer_url — {msg}")
    run_match = re.search(r"upsell run (\d+)", msg)
    plutus_run_id = int(run_match.group(1)) if run_match else None
    return {"offer_url": offer, "message": msg, "plutus_run_id": plutus_run_id}


def _local_offer_url(public_url: str) -> str:
    """Dogfood hits :8031 locally even when offers are minted with the public URL."""
    public_base = os.environ.get("PLUTUS_SAAS_PUBLIC_URL", "").rstrip("/")
    local_base = _plutus_saas_base()
    if public_base and public_url.startswith(public_base):
        return public_url.replace(public_base, local_base, 1)
    return public_url


def verify_plutus_offer(offer_url: str) -> None:
    code, body = _get(_local_offer_url(offer_url), timeout=30.0)
    if code != 200:
        raise RuntimeError(f"offer page HTTP {code}")
    if not re.search(r"package|bundle|buy", body, re.I):
        raise RuntimeError("offer page missing checkout content")


def mnemosyne_attach_offer(*, album_id: int, plutus_run_id: int) -> dict:
    base = _mnemosyne_base()
    email = os.environ.get("MNEMOSYNE_DOGFOOD_EMAIL", "")
    password = os.environ.get("MNEMOSYNE_DOGFOOD_PASSWORD", "")
    if not email or not password:
        raise RuntimeError("MNEMOSYNE_DOGFOOD_EMAIL and MNEMOSYNE_DOGFOOD_PASSWORD required")

    jar = CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))

    code, _, _ = _post_form(
        f"{base}/login",
        {"email": email, "password": password},
        opener=opener,
        timeout=30.0,
    )
    if code != 303:
        raise RuntimeError(f"mnemosyne login HTTP {code}")

    code, _, _ = _post_form(
        f"{base}/albums/{album_id}/plutus-generate",
        {"plutus_run_id": str(plutus_run_id)},
        opener=opener,
        timeout=60.0,
    )
    if code != 303:
        raise RuntimeError(f"plutus-generate HTTP {code}")

    code, body, _ = _post_form(
        f"{base}/albums/{album_id}/share",
        {},
        opener=opener,
        timeout=30.0,
    )
    if code != 303:
        raise RuntimeError(f"share mint HTTP {code}")

    db_path = os.environ.get("MNEMOSYNE_DB")
    share_token = None
    if db_path and Path(db_path).is_file():
        import sqlite3

        row = sqlite3.connect(db_path).execute(
            "SELECT share_token, plutus_offer_url FROM albums WHERE id = ?",
            (album_id,),
        ).fetchone()
        if row:
            share_token, offer_saved = row[0], row[1]
    else:
        offer_saved = None

    if not share_token:
        req = urllib.request.Request(f"{base}/albums/{album_id}")
        with opener.open(req, timeout=30.0) as resp:
            album_html = resp.read().decode(errors="replace")
            m = re.search(r"/share/([A-Za-z0-9_-]+)", album_html)
            share_token = m.group(1) if m else None

    if not share_token:
        raise RuntimeError("could not resolve mnemosyne share token")

    code, share_body = _get(f"{base}/share/{share_token}", timeout=30.0)
    if code != 200:
        raise RuntimeError(f"share page HTTP {code}")
    if "Order prints" not in share_body:
        raise RuntimeError("share page missing Order prints CTA")

    return {
        "share_url": f"{base}/share/{share_token}",
        "offer_saved": offer_saved,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Suite integration dogfood loop")
    parser.add_argument("--gallery-id", type=int, default=int(os.environ.get("MISE_GALLERY_ID", "1")))
    parser.add_argument("--mnemosyne-album-id", type=int, default=None)
    parser.add_argument("--skip-mnemosyne", action="store_true")
    parser.add_argument(
        "--plutus-only",
        action="store_true",
        help="Call Plutus Mise webhook directly (skip Argus run-all)",
    )
    args = parser.parse_args()

    _load_dotenv()
    album_id = args.mnemosyne_album_id
    if album_id is None and os.environ.get("MNEMOSYNE_ALBUM_ID"):
        album_id = int(os.environ["MNEMOSYNE_ALBUM_ID"])

    print("==> Health")
    for name, url in (
        ("argus", f"{_argus_base()}/healthz"),
        ("plutus_saas", f"{_plutus_saas_base()}/healthz"),
        ("mnemosyne", f"{_mnemosyne_base()}/healthz"),
    ):
        code, _ = _get(url)
        print(f"  {name}: HTTP {code}")
        if name != "mnemosyne" and code != 200:
            return 2

    if args.plutus_only:
        print(f"\n==> Plutus Mise webhook gallery #{args.gallery_id}")
    else:
        print(f"\n==> Argus pipeline run-all gallery #{args.gallery_id}")
    try:
        pipe = (
            run_plutus_mise_hook(args.gallery_id)
            if args.plutus_only
            else run_argus_pipeline(args.gallery_id)
        )
    except Exception as exc:
        print(f"  FAIL: {exc}")
        return 2
    print(f"  steps: {pipe['message']}")
    print(f"  offer: {pipe['offer_url']}")
    if pipe.get("plutus_run_id"):
        print(f"  plutus_run_id: {pipe['plutus_run_id']}")

    print("\n==> Plutus offer storefront")
    try:
        verify_plutus_offer(pipe["offer_url"])
    except Exception as exc:
        print(f"  FAIL: {exc}")
        return 2
    print("  storefront OK")

    result = {"pipeline": pipe, "mnemosyne": None}

    if not args.skip_mnemosyne and album_id and pipe.get("plutus_run_id"):
        print(f"\n==> Mnemosyne album #{album_id} plutus-generate + share CTA")
        try:
            result["mnemosyne"] = mnemosyne_attach_offer(
                album_id=album_id,
                plutus_run_id=int(pipe["plutus_run_id"]),
            )
        except Exception as exc:
            print(f"  FAIL: {exc}")
            return 2
        print(f"  share: {result['mnemosyne']['share_url']}")
        if result["mnemosyne"].get("offer_saved"):
            print(f"  saved offer: {result['mnemosyne']['offer_saved']}")
    elif not args.skip_mnemosyne:
        print("\n==> Mnemosyne skipped (set --mnemosyne-album-id or MNEMOSYNE_ALBUM_ID)")

    out = ROOT / "data" / f"suite-loop-{int(__import__('time').time())}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"\n==> Suite loop OK — report {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())