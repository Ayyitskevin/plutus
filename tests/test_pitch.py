from unittest.mock import MagicMock

from app import config
from app.pitch import render_pitch


def test_render_pitch_includes_bundles():
    text = render_pitch(
        gallery_name="Sample Menu",
        photo_count=12,
        estimated_total_cents=53500,
        bundles=[{
            "title": "Statement wall piece",
            "pitch": "Lead with your strongest hero.",
            "items": [{
                "label": "Canvas Wrap",
                "size": "16×20″",
                "line_cents": 18500,
                "photo": {"filename": "01-hero.jpg"},
            }],
        }],
        use_dionysus=False,
    )
    assert "Sample Menu" in text
    assert "Statement wall piece" in text
    assert "01-hero.jpg" in text
    assert "$535.00" in text


def test_render_pitch_uses_dionysus_enhancement(monkeypatch):
    monkeypatch.setattr(config, "DIONYSUS_URL", "http://dionysus.test")
    monkeypatch.setattr(config, "DIONYSUS_TOKEN", "token")
    monkeypatch.setattr(config, "DIONYSUS_ORG_SLUG", "studio")

    bundles = [{
        "title": "Statement wall piece",
        "pitch": "Static pitch.",
        "items": [{"label": "Canvas", "size": "16x20", "line_cents": 1000, "photo": {}}],
    }]

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def post(self, url, json=None, headers=None):
            resp = MagicMock()
            resp.status_code = 200
            resp.json.return_value = {
                "intro": "Dionysus intro for your gallery.",
                "bundles": [{
                    "title": "Statement wall piece",
                    "pitch": "Enhanced Dionysus pitch with keywords.",
                }],
            }
            return resp

    import httpx

    monkeypatch.setattr(httpx, "Client", FakeClient)

    text = render_pitch(
        gallery_name="Sample Menu",
        photo_count=3,
        estimated_total_cents=10000,
        bundles=bundles,
        gallery_theme="food",
    )
    assert "Dionysus intro for your gallery." in text
    assert "Enhanced Dionysus pitch with keywords." in text
    assert "Static pitch." not in text