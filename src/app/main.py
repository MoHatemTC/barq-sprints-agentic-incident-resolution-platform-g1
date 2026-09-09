from fastapi import FastAPI

from app.core.config import get_settings

settings = get_settings()

app = FastAPI(
    title="BARQ Agentic Incident Resolution Platform",
    version=settings.app_version,
)


@app.get("/")
async def health_check() -> dict[str, str]:
    return {"status": "ok"}
