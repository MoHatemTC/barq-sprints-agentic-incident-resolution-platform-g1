"""Small same-origin operator view for the existing approval API."""

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse

router = APIRouter(include_in_schema=False)
_STATIC = Path(__file__).resolve().parents[1] / "static"
_HEADERS = {
    "Cache-Control": "no-store",
    "Content-Security-Policy": (
        "default-src 'none'; script-src 'self'; style-src 'self'; "
        "connect-src 'self'; img-src 'self'; base-uri 'none'; "
        "form-action 'none'; frame-ancestors 'none'"
    ),
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
}


@router.get("/review")
def review_page() -> FileResponse:
    return FileResponse(_STATIC / "review.html", headers=_HEADERS)


@router.get("/review.css")
def review_styles() -> FileResponse:
    return FileResponse(_STATIC / "review.css", media_type="text/css", headers=_HEADERS)


@router.get("/review.js")
def review_script() -> FileResponse:
    return FileResponse(_STATIC / "review.js", media_type="text/javascript", headers=_HEADERS)
