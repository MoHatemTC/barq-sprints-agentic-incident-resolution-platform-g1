"""Tests for the same-origin operator review page.

The page is a static view over the existing approval API. It carries no state of
its own, so these tests cover what the HTTP boundary can promise: the three
assets are served, they carry the headers that keep a token out of the browser's
persistent storage and out of any framing context, and the page never appears in
the public OpenAPI document it was deliberately kept out of.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import create_app
from tests.helpers import mock_settings

_STATIC = Path(__file__).resolve().parents[1] / "src" / "api" / "static"
_ASSETS = {
    "/review": ("review.html", "text/html"),
    "/review.css": ("review.css", "text/css"),
    "/review.js": ("review.js", "text/javascript"),
}


@pytest.fixture
def client():
    app = create_app(settings=mock_settings())
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
@pytest.mark.parametrize("route", list(_ASSETS))
async def test_review_assets_are_served(client, route) -> None:
    filename, media_type = _ASSETS[route]
    async with client as http:
        response = await http.get(route)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith(media_type)
    assert response.text.strip(), f"{filename} is empty"


@pytest.mark.asyncio
@pytest.mark.parametrize("route", list(_ASSETS))
async def test_review_assets_need_no_credential(client, route) -> None:
    """The page is static; only the API calls behind it require a token."""
    async with client as http:
        response = await http.get(route)

    assert response.status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize("route", list(_ASSETS))
async def test_review_assets_carry_the_lockdown_headers(client, route) -> None:
    async with client as http:
        response = await http.get(route)

    headers = response.headers
    assert headers["cache-control"] == "no-store"
    assert headers["x-content-type-options"] == "nosniff"
    assert headers["referrer-policy"] == "no-referrer"
    csp = headers["content-security-policy"]
    assert "default-src 'none'" in csp
    assert "frame-ancestors 'none'" in csp
    assert "form-action 'none'" in csp
    # No inline script or style is permitted, so the page cannot be refactored
    # into one by accident.
    assert "script-src 'self'" in csp
    assert "style-src 'self'" in csp
    assert "unsafe-inline" not in csp


@pytest.mark.asyncio
async def test_review_page_is_absent_from_the_openapi_document(client) -> None:
    """An operator surface stays out of the published contract on purpose."""
    async with client as http:
        document = (await http.get("/openapi.json")).json()

    assert not [path for path in document["paths"] if "review" in path]


@pytest.mark.asyncio
async def test_review_page_does_not_embed_a_credential(client) -> None:
    async with client as http:
        body = (await http.get("/review")).text
        script = (await http.get("/review.js")).text

    combined = body + script
    assert not re.search(r"eyJ[A-Za-z0-9_-]{10,}", combined), "a JWT is baked into the page"
    # The token is read from a field and held in a variable, never persisted.
    assert "localStorage" not in combined
    assert "sessionStorage" not in combined
    assert "document.cookie" not in combined


def test_review_markup_references_only_its_own_assets() -> None:
    html = (_STATIC / "review.html").read_text()
    for source in re.findall(r'(?:src|href)="([^"]+)"', html):
        assert source.startswith("/review"), f"unexpected asset reference: {source}"
        assert (_STATIC / source.removeprefix("/")).is_file()
