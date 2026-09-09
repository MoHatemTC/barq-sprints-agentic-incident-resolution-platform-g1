from fastapi import FastAPI

app = FastAPI(
    title="BARQ Agentic Incident Resolution Platform",
    version="0.1.0",
)


@app.get("/")
async def health_check() -> dict[str, str]:
    return {"status": "ok"}
