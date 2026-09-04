"""
Config parsing that has bitten me in production.

Nothing here needs a database or a model. These are the small string
mistakes that make a healthy service look broken.
"""

from app.config import _origins


def test_a_trailing_slash_is_stripped_from_an_origin():
    """
    The bug that cost an evening on the first deploy.

    A browser sends Origin as scheme + host + port, never with a trailing
    slash, and CORSMiddleware compares exactly. So a copied-from-the-
    address-bar "https://x.vercel.app/" refuses every request while the
    API reports itself perfectly healthy and curl works, because curl
    sends no Origin header at all.
    """
    assert _origins("https://sql-agent.vercel.app/") == ["https://sql-agent.vercel.app"]


def test_several_origins_split_and_are_each_cleaned():
    parsed = _origins("http://localhost:5173/, https://x.vercel.app/ ,https://y.app")
    assert parsed == ["http://localhost:5173", "https://x.vercel.app", "https://y.app"]


def test_blank_entries_are_dropped():
    """A trailing comma in a dashboard field must not become an empty origin."""
    assert _origins("https://x.app,,  ,") == ["https://x.app"]
