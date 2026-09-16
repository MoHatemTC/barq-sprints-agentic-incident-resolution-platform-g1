from fastapi import FastAPI

from app.core.config import get_settings
from app.core.logging import configure_logging

settings = get_settings()
configure_logging(environment=settings.environment, log_level=settings.log_level)

app = FastAPI(
    title="BARQ Agentic Incident Resolution Platform",
    version=settings.app_version,
)


@app.get("/")
async def health_check() -> dict[str, str]:
    return {"status": "ok"}
