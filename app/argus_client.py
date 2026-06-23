"""Argus vision client — folder analyze + job polling for upload enrichment."""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

import httpx

from . import config

log = logging.getLogger("plutus.argus")


class ArgusClientError(Exception):
    """Human-readable Argus API failure."""


def is_enabled() -> bool:
    return bool(config.ARGUS_URL and config.ARGUS_TOKEN)


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {config.ARGUS_TOKEN}"}


def vision_status() -> dict[str, Any]:
    if not is_enabled():
        return {"configured": False, "reachable": False}
    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.get(f"{config.ARGUS_URL}/vision/status", headers=_headers())
        if resp.status_code >= 400:
            return {"configured": True, "reachable": False, "detail": f"HTTP {resp.status_code}"}
        body = resp.json()
        return {
            "configured": True,
            "reachable": True,
            "backend": body.get("backend"),
            "model": body.get("model"),
            "provider": body.get("provider") or body.get("vision_provider"),
        }
    except httpx.HTTPError as exc:
        log.warning("Argus vision status unreachable: %s", exc)
        return {"configured": True, "reachable": False, "detail": str(exc)}


def _run_id_from_job(job: dict[str, Any]) -> int | None:
    if job.get("run_id"):
        return int(job["run_id"])
    raw = job.get("result")
    if not raw:
        return None
    try:
        result = json.loads(raw) if isinstance(raw, str) else raw
    except (json.JSONDecodeError, TypeError):
        return None
    rid = result.get("run_id")
    return int(rid) if rid else None


def _poll_job(job_id: str) -> dict[str, Any]:
    deadline = time.time() + config.ARGUS_TIMEOUT
    interval = 2.0
    url = f"{config.ARGUS_URL}/jobs/{job_id}"
    with httpx.Client(timeout=30.0) as client:
        while time.time() < deadline:
            resp = client.get(url, headers=_headers())
            if resp.status_code == 404:
                raise ArgusClientError(f"Argus job not found: {job_id}")
            if resp.status_code >= 400:
                raise ArgusClientError(f"Argus job poll HTTP {resp.status_code}")
            job = resp.json()
            status = job.get("status") or ""
            if status in {"done", "failed", "dead_letter"}:
                return job
            time.sleep(interval)
    raise ArgusClientError(f"Argus job {job_id} timed out after {config.ARGUS_TIMEOUT}s")


def analyze_folder(
    folder: Path,
    *,
    limit: int | None = None,
    client_id: str | None = None,
) -> int:
    """Run Argus vision on a local folder; returns Argus run_id."""
    if not is_enabled():
        raise ArgusClientError("Argus is not configured")
    path = folder.expanduser().resolve()
    if not path.is_dir():
        raise ArgusClientError(f"folder not found: {path}")

    effective_limit = limit if limit is not None else config.ARGUS_ANALYZE_LIMIT
    data: dict[str, str] = {
        "folder": str(path),
        "limit": str(effective_limit),
    }
    if client_id:
        data["client_id"] = client_id

    url = f"{config.ARGUS_URL}/analyze-folder"
    try:
        with httpx.Client(timeout=60.0) as client:
            resp = client.post(url, data=data, headers=_headers())
    except httpx.TimeoutException as exc:
        raise ArgusClientError(f"Argus analyze-folder timed out: {exc}") from exc
    except httpx.RequestError as exc:
        raise ArgusClientError(f"Argus unreachable: {exc}") from exc

    if resp.status_code >= 400:
        detail = resp.text[:300]
        raise ArgusClientError(f"Argus analyze-folder HTTP {resp.status_code}: {detail}")

    body = resp.json()
    if body.get("run_id"):
        return int(body["run_id"])

    if body.get("mode") == "queued" and body.get("job_id"):
        job = _poll_job(str(body["job_id"]))
        if job.get("status") != "done":
            raise ArgusClientError(f"Argus job failed: {job.get('status')}")
        run_id = _run_id_from_job(job)
        if not run_id:
            raise ArgusClientError("Argus job completed without run_id")
        return run_id

    raise ArgusClientError("Argus analyze-folder returned no run_id")