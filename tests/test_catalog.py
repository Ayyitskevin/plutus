from app.catalog import get_product


def test_catalog_has_album():
    p = get_product("album-20")
    assert p is not None
    assert p.category == "album"
    assert p.unit_cents > 0