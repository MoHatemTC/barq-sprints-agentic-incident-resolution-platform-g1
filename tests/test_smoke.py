"""Smoke tests that exercise the app through FastAPI's TestClient."""

from fastapi.testclient import TestClient

from app.main import app


def test_health_check_returns_ok() -> None:
    """GET /health returns 200 and the documented body."""
    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_openapi_schema_is_served() -> None:
    """The app builds a valid OpenAPI document."""
    with TestClient(app) as client:
        response = client.get("/openapi.json")

    assert response.status_code == 200
    schema = response.json()
    assert schema["info"]["title"] == "BARQ Agentic Incident Resolution Platform"
    assert "/health" in schema["paths"]
