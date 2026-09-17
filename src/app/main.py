from fastapi import FastAPI

from api.routers.approvals import router as approvals_router
from api.routers.config import router as config_router
from api.routers.dlq import router as dlq_router
from api.routers.eval import router as eval_router
from api.routers.executions import router as executions_router
from api.routers.health import router as health_router
from api.routers.webhook import router as webhook_router
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
        description=(
            "Autonomous incident triage, diagnosis, and remediation platform with HITL governance."
        ),
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

    # Register routers
    app.include_router(health_router)
    app.include_router(webhook_router)
    app.include_router(executions_router)
    app.include_router(approvals_router)
    app.include_router(config_router)
    app.include_router(dlq_router)
    app.include_router(eval_router)

    return app


# Production ASGI instance
app = create_app()

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
