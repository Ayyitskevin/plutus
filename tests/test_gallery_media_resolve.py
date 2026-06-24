"""resolve_photo_file containment — a path_hint may not redirect to another file.

The served filename is validated against the offer's photo set, but the stored
path_hint is a separate field. If those two could disagree, a tampered or buggy
payload could serve any file the process can read under the cover of a valid
token. The resolver pins them together: path_hint is honored only when its
basename matches the validated filename."""
from __future__ import annotations

import pytest

from app import gallery_media


def _payload(filename: str, path_hint: str) -> dict:
    return {
        "bundles": [
            {"items": [{"photo": {"filename": filename, "path": path_hint}}]}
        ]
    }


def test_path_hint_to_other_file_is_rejected(tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("not a photo")
    payload = _payload("hero.jpg", str(secret))
    # hero.jpg is in the offer, but the path_hint points at secret.txt — the
    # basename mismatch means we never serve it, and with no other candidate the
    # lookup fails closed.
    with pytest.raises(gallery_media.GalleryMediaError):
        gallery_media.resolve_photo_file(gallery=None, payload=payload, filename="hero.jpg")


def test_legit_path_hint_is_served(tmp_path):
    photo = tmp_path / "hero.jpg"
    photo.write_bytes(b"\xff\xd8\xff\xe0jpegish")
    payload = _payload("hero.jpg", str(photo))
    resolved = gallery_media.resolve_photo_file(
        gallery=None, payload=payload, filename="hero.jpg"
    )
    assert resolved == photo
