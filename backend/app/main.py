"""Titik masuk aplikasi FastAPI."""

import logging
import time

from fastapi import FastAPI, Response, status
from pydantic import BaseModel
from sqlalchemy import text

from app import __version__
from app.core.config import get_settings
from app.core.db import get_engine
from app.core.ports.providers import warn_unsafe_backends

# Mendaftarkan listener `after_begin` (set_config app.tenant_id per transaksi) untuk semua Session.
from app.core.tenancy import session as _tenancy_session  # noqa: F401
from app.framework.errors import register_record_error_handlers

logger = logging.getLogger(__name__)


class HealthResponse(BaseModel):
    status: str
    app: str
    version: str
    environment: str


class DatabaseHealthResponse(BaseModel):
    status: str
    database: str | None = None
    latency_ms: float | None = None


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=f"{settings.app_name} API",
        version=__version__,
        openapi_url=f"{settings.api_prefix}/openapi.json",
        docs_url=f"{settings.api_prefix}/docs",
        redoc_url=None,
    )
    register_record_error_handlers(app)
    warn_unsafe_backends(settings)

    @app.get(f"{settings.api_prefix}/health", response_model=HealthResponse, tags=["system"])
    async def health() -> HealthResponse:
        return HealthResponse(
            status="ok",
            app=settings.app_name,
            version=__version__,
            environment=settings.environment,
        )

    @app.get(
        f"{settings.api_prefix}/health/db",
        response_model=DatabaseHealthResponse,
        tags=["system"],
        responses={503: {"model": DatabaseHealthResponse}},
    )
    async def health_db(response: Response) -> DatabaseHealthResponse:
        """Readiness: menjalankan query nyata ke database (Neon) dan mengukur latensinya."""
        if not settings.database_url:
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
            return DatabaseHealthResponse(status="not_configured")
        started = time.perf_counter()
        try:
            async with get_engine().connect() as conn:
                database = (await conn.execute(text("SELECT current_database()"))).scalar_one()
        except Exception:
            logger.exception("Database health check failed")
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
            return DatabaseHealthResponse(status="error")
        latency = round((time.perf_counter() - started) * 1000, 1)
        return DatabaseHealthResponse(status="ok", database=database, latency_ms=latency)

    return app


app = create_app()
