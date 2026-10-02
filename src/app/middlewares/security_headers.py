"""Baseline security headers for every response.

JSON API responses carried none (the ``/review`` page set its own). They are cheap, and a
response that may hold an approval brief or a token should not be sniffed, framed, cached
or leak its URL through ``Referer``. Headers a route already set (the review page's own
Content-Security-Policy) are never overridden. ``Strict-Transport-Security`` is
deliberately absent: the deployment serves plain HTTP, and HSTS over HTTP is ignored.
"""

from __future__ import annotations

from collections.abc import Callable

from fastapi import FastAPI, Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

_COMMON = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
}
#: Machine-readable API responses: never cached, never rendered as a document.
_API = {
    "Cache-Control": "no-store",
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
}
_API_PREFIXES = ("/api/", "/health", "/ready")


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        response: Response = await call_next(request)
        headers = dict(_COMMON)
        if request.url.path.startswith(_API_PREFIXES):
            headers.update(_API)
        for name, value in headers.items():
            if name not in response.headers:
                response.headers[name] = value
        return response


def register_security_headers(app: FastAPI) -> None:
    app.add_middleware(SecurityHeadersMiddleware)


__all__ = ["SecurityHeadersMiddleware", "register_security_headers"]
