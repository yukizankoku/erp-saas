"""Titik masuk aplikasi FastAPI."""

from fastapi import FastAPI
from pydantic import BaseModel

from app import __version__
from app.core.config import get_settings


class HealthResponse(BaseModel):
    status: str
    app: str
    version: str
    environment: str


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=f"{settings.app_name} API",
        version=__version__,
        openapi_url=f"{settings.api_prefix}/openapi.json",
        docs_url=f"{settings.api_prefix}/docs",
        redoc_url=None,
    )

    @app.get(f"{settings.api_prefix}/health", response_model=HealthResponse, tags=["system"])
    async def health() -> HealthResponse:
        return HealthResponse(
            status="ok",
            app=settings.app_name,
            version=__version__,
            environment=settings.environment,
        )

    return app


app = create_app()
