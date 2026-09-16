from fastapi import FastAPI


from app.core.config import Settings, get_settings
from app.core.lifespan import lifespan
from app.core.logging import configure_logging
from app.exceptions.handlers import register_exception_handlers
from app.middlewares.correlation import register_middlewares



def create_app(settings: Settings | None = None) -> FastAPI:
    """Construct and configure the production FastAPI application instance."""
    app_settings = settings or get_settings()

    configure_logging(
        environment=app_settings.environment,
        log_level=app_settings.log_level,
    )

    app = FastAPI(
        title="BARQ Agentic Incident Resolution Platform",
        description="Autonomous incident triage, diagnosis, and remediation platform with HITL governance.",
        version=app_settings.app_version,
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
    )

    # Attach settings to application state
    app.state.settings = app_settings

    # Register middlewares (correlation ID, access logging)
    register_middlewares(app)

    # Register global exception handlers (unified JSON envelope)
    register_exception_handlers(app)

    return app


# Production ASGI instance
app = create_app()

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)

