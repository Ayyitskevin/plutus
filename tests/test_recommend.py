from app.recommend import recommend_bundles


def _photo(name: str, keeper: float = 0.9, hero: float = 0.85) -> dict:
    return {
        "filename": name,
        "path": f"/tmp/{name}",
        "width": 4000,
        "height": 3000,
        "orientation": "landscape",
        "keeper_score": keeper,
        "hero_potential": hero,
        "shot_type": "hero_plate",
        "keywords": [],
    }


def test_recommend_returns_bundles():
    photos = [_photo(f"{i:02d}.jpg", keeper=0.7 + i * 0.02) for i in range(18)]
    result = recommend_bundles(photos)
    assert result["photo_count"] == 18
    assert len(result["bundles"]) >= 3
    assert result["estimated_total_cents"] > 0


def test_empty_gallery():
    result = recommend_bundles([])
    assert result["bundles"] == []
    assert result["estimated_total_cents"] == 0