"""FastAPI application entry point."""

from __future__ import annotations

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.api import (
    audit,
    auth,
    dashboards,
    demo,
    institutions,
    mock_apaar,
    review,
    rules_admin,
    students,
    uploads,
    users,
    validation,
)
from app.core.config import settings
from app.core.errors import install_error_handlers
from app.db.session import engine

# Routes reachable without authentication (FR-36). Later phases add /verify,
# /.well-known/public-key and /revocations here.
PUBLIC_PATHS = {
    "/health",
    f"{settings.api_prefix}/health",
    f"{settings.api_prefix}/auth/login",
    f"{settings.api_prefix}/auth/refresh",
}


def health() -> dict:
    """Liveness + DB connectivity. Public."""
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        db_status = "ok"
    except Exception:  # pragma: no cover - only when the DB is down
        db_status = "unavailable"
    return {
        "status": "ok" if db_status == "ok" else "degraded",
        "db": db_status,
        "version": settings.app_version,
    }


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        description=(
            "APAAR Ledger prototype API (SIH 2026, SIH26263). "
            "Authenticate with POST /api/v1/auth/login (username = email)."
        ),
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
    )
    install_error_handlers(app)

    api = APIRouter(prefix=settings.api_prefix)
    api.add_api_route("/health", health, methods=["GET"], tags=["ops"])
    api.include_router(auth.router)
    api.include_router(institutions.router)
    api.include_router(users.router)
    api.include_router(mock_apaar.router)
    api.include_router(validation.router)
    api.include_router(students.router)
    api.include_router(review.router)
    api.include_router(rules_admin.router)
    api.include_router(uploads.router)
    api.include_router(dashboards.router)
    api.include_router(audit.router)
    api.include_router(demo.router)
    app.include_router(api)
    app.add_api_route("/health", health, methods=["GET"], tags=["ops"], include_in_schema=False)
    return app


app = create_app()
