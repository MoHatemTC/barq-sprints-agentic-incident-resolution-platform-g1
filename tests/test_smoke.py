"""#74: the smoke test asserted `app is not None` and never ran a request.

`main.py`'s route handler was the only uncovered line in the file, so a broken
handler, a bad response model or a serialisation error would all have passed. The
test now exercises the app through FastAPI's TestClient.
"""

from fastapi.testclient import TestClient

from app.main import app


def test_health_check_returns_ok() -> None:
    """GET / returns 200 and the documented body."""
    with TestClient(app) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_openapi_schema_is_served() -> None:
    """The app builds a valid OpenAPI document.

    Sprint 2's S2.1 grading is on the generated OpenAPI surface, so a schema that
    fails to build should fail here rather than at the demo.
    """
    with TestClient(app) as client:
        response = client.get("/openapi.json")

    assert response.status_code == 200
    schema = response.json()
    assert schema["info"]["title"] == "BARQ Agentic Incident Resolution Platform"
    assert "/" in schema["paths"]
