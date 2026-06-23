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
    )
    assert "Sample Menu" in text
    assert "Statement wall piece" in text
    assert "01-hero.jpg" in text
    assert "$535.00" in text