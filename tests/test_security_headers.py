"""Every response carries baseline security headers; routes may still add their own."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import create_app
from tests.helpers import mock_settings


@pytest.fixture
def client() -> AsyncClient:
    app = create_app(settings=mock_settings())
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/health", "/api/v1/approvals", "/no-such-route"])
async def test_api_and_error_responses_carry_baseline_headers(client, path: str) -> None:
    async with client as c:
        response = await c.get(path)
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "no-referrer"


@pytest.mark.asyncio
async def test_api_responses_are_not_cacheable_or_renderable(client) -> None:
    async with client as c:
        response = await c.get("/health")
    assert response.headers["cache-control"] == "no-store"
    assert "default-src 'none'" in response.headers["content-security-policy"]


@pytest.mark.asyncio
async def test_a_route_that_sets_its_own_policy_keeps_it(client) -> None:
    async with client as c:
        response = await c.get("/review")
    # The review page serves its own, stricter-for-a-page policy; it is not overwritten.
    assert "script-src 'self'" in response.headers["content-security-policy"]


@pytest.mark.asyncio
async def test_docs_pages_are_not_given_the_api_csp(client) -> None:
    async with client as c:
        response = await c.get("/docs")
    assert "content-security-policy" not in response.headers
    assert response.headers["x-content-type-options"] == "nosniff"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "supplied",
    ["x" * 129, "bad value with spaces", "line\tbreak", "<script>alert(1)</script>", ""],
)
async def test_an_unusable_correlation_id_is_replaced(client, supplied: str) -> None:
    async with client as c:
        response = await c.get("/health", headers={"X-Correlation-ID": supplied})
    echoed = response.headers["x-correlation-id"]
    assert echoed != supplied and len(echoed) == 36  # a fresh UUID


@pytest.mark.asyncio
async def test_a_reasonable_correlation_id_is_preserved(client) -> None:
    async with client as c:
        response = await c.get("/health", headers={"X-Correlation-ID": "snow-INC0010233.v2:1"})
    assert response.headers["x-correlation-id"] == "snow-INC0010233.v2:1"


@pytest.mark.asyncio
async def test_a_replaced_correlation_id_is_logged_with_a_bounded_copy(client) -> None:
    from structlog.testing import capture_logs

    with capture_logs() as logs:
        async with client as c:
            response = await c.get("/health", headers={"X-Correlation-ID": "abc/def==" + "x" * 500})
    replaced = [log for log in logs if log["event"] == "correlation_id_replaced"]
    assert len(replaced) == 1
    assert len(replaced[0]["supplied"]) <= 70
    assert replaced[0]["correlation_id"] == response.headers["x-correlation-id"]
