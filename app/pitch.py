"""Client-facing upsell pitch text (copy-paste email)."""
from __future__ import annotations

from typing import Any


def _money(cents: int) -> str:
    return f"${cents / 100:,.2f}"


def render_pitch(
    *,
    gallery_name: str,
    bundles: list[dict[str, Any]],
    estimated_total_cents: int,
    photo_count: int,
) -> str:
    lines = [
        f"Hi — your gallery \"{gallery_name}\" is ready, and a few print ideas stood out.",
        "",
        f"I pulled {photo_count} photos into {len(bundles)} bundle"
        f"{'' if len(bundles) == 1 else 's'} you might love:",
        "",
    ]
    for bundle in bundles:
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