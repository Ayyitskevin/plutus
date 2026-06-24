"""Client-facing upsell pitch text (copy-paste email)."""
from __future__ import annotations

from typing import Any


def _money(cents: int) -> str:
    return f"${cents / 100:,.2f}"


def _apply_enhancement(
    bundles: list[dict[str, Any]],
    enhancement: dict[str, Any] | None,
) -> tuple[str | None, list[dict[str, Any]]]:
    if not enhancement:
        return None, bundles
    merged = [dict(bundle) for bundle in bundles]
    by_title = {
        str(row.get("title") or ""): str(row.get("pitch") or "").strip()
        for row in enhancement.get("bundles") or []
        if row.get("title")
    }
    for bundle in merged:
        title = str(bundle.get("title") or "")
        if title in by_title and by_title[title]:
            bundle["pitch"] = by_title[title]
    intro = enhancement.get("intro")
    return (str(intro).strip() if intro else None), merged


def render_pitch(
    *,
    gallery_name: str,
    bundles: list[dict[str, Any]],
    estimated_total_cents: int,
    photo_count: int,
    gallery_theme: str | None = None,
    argus_run_id: int | None = None,
    use_dionysus: bool = True,
) -> str:
    intro_override: str | None = None
    working = bundles
    if use_dionysus:
        from . import dionysus_client

        enhancement = dionysus_client.enhance_pitch(
            gallery_name=gallery_name,
            bundles=bundles,
            estimated_total_cents=estimated_total_cents,
            photo_count=photo_count,
            gallery_theme=gallery_theme,
            argus_run_id=argus_run_id,
        )
        intro_override, working = _apply_enhancement(bundles, enhancement)

    if intro_override:
        lines = [intro_override, ""]
    else:
        lines = [
            f'Hi — your gallery "{gallery_name}" is ready, and a few print ideas stood out.',
            "",
        ]

    lines.extend([
        f"I pulled {photo_count} photos into {len(working)} bundle"
        f"{'' if len(working) == 1 else 's'} you might love:",
        "",
    ])
    for bundle in working:
        lines.append(f"▸ {bundle.get('title', 'Bundle')}")
        pitch = bundle.get("pitch")
        if pitch:
            lines.append(f"  {pitch}")
        for item in bundle.get("items") or []:
            photo = (item.get("photo") or {}).get("filename", "")
            lines.append(
                f"  · {item.get('label')} {item.get('size')} — {photo} "
                f"({_money(int(item.get('line_cents') or 0))})"
            )
        lines.append("")

    lines.extend([
        f"Estimated bundle total (before shipping): {_money(estimated_total_cents)}",
        "",
        "Reply with which bundle you'd like and I'll send a checkout link.",
        "",
        "— Kevin",
    ])
    return "\n".join(lines).strip() + "\n"