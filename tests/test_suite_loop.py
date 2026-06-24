"""Suite loop helpers — URL parsing."""
from __future__ import annotations

import urllib.parse


def test_parse_pipeline_redirect():
    loc = (
        "/ui/pipeline?msg=vision+run+5%3B+upsell+run+42+%283+bundles%29"
        "%3B+offer+link+ready&offer_url=https%3A%2F%2Fplutus.test%2Fstore%2Fx%2Foffer%2Ft"
    )
    qs = urllib.parse.parse_qs(urllib.parse.urlparse(loc).query)
    offer = urllib.parse.unquote_plus(qs["offer_url"][0])
    msg = urllib.parse.unquote_plus(qs["msg"][0])
    assert offer.startswith("https://")
    assert "upsell run 42" in msg