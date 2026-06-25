#!/usr/bin/env python3
"""Studio integration loop: Mise → Argus → Plutus review + pitch.

Usage:
  python scripts/dogfood_suite_loop.py
  python scripts/dogfood_suite_loop.py --gallery-id 1
  python scripts/dogfood_suite_loop.py --plutus-only --gallery-id 1

Env (from plutus .env.homelab / argus .env or shell):
  ARGUS_API_TOKEN, ARGUS_HOST, ARGUS_PORT
  PLUTUS_URL or ARGUS_PLUTUS_URL (default http://127.0.0.1:8030)
  PLUTUS_API_TOKEN or ARGUS_PLUTUS_TOKEN — for direct recommend (--plutus-only)

Optional Mnemosyne (--mnemosyne-album-id):
  MNEMOSYNE_URL, MNEMOSYNE_DOGFOOD_EMAIL, MNEMOSYNE_DOGFOOD_PASSWORD
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
    _load_env_file(ROOT / ".env.homelab")
    _load_env_file(ROOT / ".env")
    _load_env_file(ROOT.parent / "argus" / ".env")
    _load_env_file(ROOT.parent / "mnemosyne" / ".env")


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


def _cookie_opener_no_redirect() -> tuple[urllib.request.OpenerDirector, CookieJar]:
    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None

    jar = CookieJar()
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(jar), _NoRedirect()
    )
    return opener, jar


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


def _plutus_base() -> str:
    return (
        os.environ.get("PLUTUS_URL")
        or os.environ.get("ARGUS_PLUTUS_URL")
        or os.environ.get("PLUTUS_SAAS_URL")
        or "http://127.0.0.1:8030"
    ).rstrip("/")


def _plutus_token() -> str:
    return (
        os.environ.get("PLUTUS_API_TOKEN")
        or os.environ.get("ARGUS_PLUTUS_TOKEN")
        or os.environ.get("PLUTUS_MISE_HOOK_TOKEN")
        or _token()
    )


def _mnemosyne_base() -> str:
    port = os.environ.get("MNEMOSYNE_PORT", "8000")
    return os.environ.get("MNEMOSYNE_URL", f"http://127.0.0.1:{port}").rstrip("/")


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


def resolve_album_for_mise_gallery(gallery_id: int) -> int | None:
    """Return a ready mnemosyne album linked to this Mise gallery, if any."""
    db_path = os.environ.get("MNEMOSYNE_DB") or str(
        ROOT.parent / "mnemosyne" / "mnemosyne.db"
    )
    if not Path(db_path).is_file():
        return None
    import sqlite3

    row = sqlite3.connect(db_path).execute(
        "SELECT id FROM albums WHERE mise_gallery_id = ? AND status = 'ready' "
        "ORDER BY id DESC LIMIT 1",
        (gallery_id,),
    ).fetchone()
    return int(row[0]) if row else None


def run_plutus_studio_recommend(gallery_id: int) -> dict:
    """Direct Plutus studio recommend — skips Argus run-all."""
    token = _plutus_token()
    if not token:
        raise RuntimeError("PLUTUS_API_TOKEN or ARGUS_PLUTUS_TOKEN required")
    fields = {"mise_gallery_id": str(gallery_id)}
    argus_run = os.environ.get("ARGUS_RUN_ID")
    if argus_run:
        fields["argus_run_id"] = argus_run
    code, body, _ = _post_form(
        f"{_plutus_base()}/recommend/mise-gallery",
        fields,
        headers={"Authorization": f"Bearer {token}"},
        timeout=120.0,
    )
    if code != 200:
        raise RuntimeError(f"plutus recommend HTTP {code}: {body[:240]}")
    payload = json.loads(body)
    return _studio_result_from_payload(payload)


def _studio_result_from_payload(payload: dict) -> dict:
    run_id = payload.get("run_id")
    review = payload.get("review_url") or ""
    pitch = payload.get("pitch_url") or ""
    if run_id and not review:
        base = _plutus_base()
        review = f"{base}/runs/{run_id}"
        pitch = f"{base}/runs/{run_id}/pitch.txt"
    bundles = payload.get("bundles") or []
    bundle_n = payload.get("bundle_count") or len(bundles)
    return {
        "review_url": review,
        "pitch_url": pitch,
        "message": f"bundles run {run_id} ({bundle_n} bundles)",
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
    review = urllib.parse.unquote_plus((qs.get("review_url") or [""])[0])
    pitch = urllib.parse.unquote_plus((qs.get("pitch_url") or [""])[0])
    msg = urllib.parse.unquote_plus((qs.get("msg") or [""])[0])
    if not review or not pitch:
        raise RuntimeError(f"pipeline returned no review/pitch — {msg}")
    run_match = re.search(r"bundles (?:run (\d+)|skipped \(run (\d+)\))", msg)
    plutus_run_id = None
    if run_match:
        plutus_run_id = int(run_match.group(1) or run_match.group(2))
    return {
        "review_url": review,
        "pitch_url": pitch,
        "message": msg,
        "plutus_run_id": plutus_run_id,
    }


def verify_studio_run(*, review_url: str, pitch_url: str) -> None:
    code, body = _get(review_url, timeout=30.0)
    if code != 200:
        raise RuntimeError(f"review page HTTP {code}")
    if not re.search(r"Upsell bundles|bundle", body, re.I):
        raise RuntimeError("review page missing bundle content")
    code, pitch = _get(pitch_url, timeout=30.0)
    if code != 200:
        raise RuntimeError(f"pitch HTTP {code}")
    if len(pitch.strip()) < 20:
        raise RuntimeError("pitch.txt too short")


def mnemosyne_attach_offer(*, album_id: int, plutus_run_id: int) -> dict:
    base = _mnemosyne_base()
    email = os.environ.get("MNEMOSYNE_DOGFOOD_EMAIL", "")
    password = os.environ.get("MNEMOSYNE_DOGFOOD_PASSWORD", "")
    if not email or not password:
        raise RuntimeError("MNEMOSYNE_DOGFOOD_EMAIL and MNEMOSYNE_DOGFOOD_PASSWORD required")

    opener, _jar = _cookie_opener_no_redirect()

    code, _, _ = _post_form(
        f"{base}/login",
        {"email": email, "password": password},
        opener=opener,
        timeout=30.0,
        allow_redirects=False,
    )
    if code != 303:
        raise RuntimeError(f"mnemosyne login HTTP {code}")

    code, _, meta = _post_form(
        f"{base}/albums/{album_id}/plutus-generate",
        {"plutus_run_id": str(plutus_run_id)},
        opener=opener,
        timeout=60.0,
        allow_redirects=False,
    )
    if code != 303:
        raise RuntimeError(f"plutus-generate HTTP {code}")
    if "plutus_error" in (meta.get("location") or ""):
        raise RuntimeError(
            f"plutus-generate failed: {urllib.parse.unquote_plus(meta.get('location', ''))}"
        )

    code, _, _ = _post_form(
        f"{base}/albums/{album_id}/share",
        {},
        opener=opener,
        timeout=30.0,
        allow_redirects=False,
    )
    if code != 303:
        raise RuntimeError(f"share mint HTTP {code}")

    db_path = os.environ.get("MNEMOSYNE_DB") or str(
        ROOT.parent / "mnemosyne" / "mnemosyne.db"
    )
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
        help="Call Plutus /recommend/mise-gallery directly (skip Argus run-all)",
    )
    parser.add_argument(
        "--with-mnemosyne",
        action="store_true",
        help="Also run Mnemosyne plutus-generate + share CTA (needs album id)",
    )
    args = parser.parse_args()

    _load_dotenv()
    album_id = args.mnemosyne_album_id
    if album_id is None and os.environ.get("MNEMOSYNE_ALBUM_ID"):
        album_id = int(os.environ["MNEMOSYNE_ALBUM_ID"])
    want_mnemosyne = args.with_mnemosyne and not args.skip_mnemosyne
    if album_id is None and want_mnemosyne:
        album_id = resolve_album_for_mise_gallery(args.gallery_id)
        if album_id:
            print(f"==> Mnemosyne album #{album_id} (mise gallery #{args.gallery_id})")

    print("==> Health")
    for name, url in (
        ("argus", f"{_argus_base()}/healthz"),
        ("plutus", f"{_plutus_base()}/healthz"),
        ("mnemosyne", f"{_mnemosyne_base()}/healthz"),
    ):
        code, _ = _get(url)
        print(f"  {name}: HTTP {code}")
        if name != "mnemosyne" and code != 200:
            return 2

    if args.plutus_only:
        print(f"\n==> Plutus studio recommend gallery #{args.gallery_id}")
    else:
        print(f"\n==> Argus pipeline run-all gallery #{args.gallery_id}")
    try:
        pipe = (
            run_plutus_studio_recommend(args.gallery_id)
            if args.plutus_only
            else run_argus_pipeline(args.gallery_id)
        )
    except Exception as exc:
        print(f"  FAIL: {exc}")
        return 2
    print(f"  steps: {pipe['message']}")
    print(f"  review: {pipe['review_url']}")
    print(f"  pitch: {pipe['pitch_url']}")
    if pipe.get("plutus_run_id"):
        print(f"  plutus_run_id: {pipe['plutus_run_id']}")

    print("\n==> Plutus studio review + pitch")
    try:
        verify_studio_run(
            review_url=pipe["review_url"],
            pitch_url=pipe["pitch_url"],
        )
    except Exception as exc:
        print(f"  FAIL: {exc}")
        return 2
    print("  studio OK")

    result = {"pipeline": pipe, "mnemosyne": None}

    if want_mnemosyne and album_id and pipe.get("plutus_run_id"):
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
    elif want_mnemosyne:
        if not album_id:
            print("\n==> Mnemosyne skipped (set --mnemosyne-album-id or MNEMOSYNE_ALBUM_ID)")
        else:
            print("\n==> Mnemosyne skipped (pipeline message had no plutus run id)")

    out = ROOT / "data" / f"suite-loop-{int(__import__('time').time())}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"\n==> Suite loop OK — report {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())