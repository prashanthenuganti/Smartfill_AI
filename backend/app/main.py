"""
app/main.py
-----------
FastAPI application entry point for SmartFill AI.

Responsibilities:
  - Create the FastAPI app instance
  - Configure CORS (Chrome Extension origin)
  - Register global exception handlers (converts unhandled errors to ErrorResponse)
  - Mount the v1 router
  - Run logging configuration at startup via lifespan
  - Expose `app` for uvicorn

Run locally:
    uvicorn backend.app.main:app --reload --host 127.0.0.1 --port 8000
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from backend.app.api.v1.routes import router
from backend.app.core.config import get_settings
from backend.app.core.logging import configure_logging, get_logger
from backend.app.schemas.errors import ErrorCode, ErrorResponse

# Logger obtained after configure_logging() is called in lifespan
logger = get_logger(__name__)


# ── Lifespan ──────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Runs at startup (before first request) and shutdown (after last request).

    Startup:
      - Configure logging
      - Log settings summary (no PII)
      - Pre-warm the pipeline (loads Tesseract, instantiates parsers)

    Shutdown:
      - Log graceful shutdown message
    """
    # ── Startup ───────────────────────────────────────────────────────────────
    configure_logging()
    settings = get_settings()

    logger.info("=" * 60)
    logger.info("SmartFill AI starting up")
    logger.info("  environment : %s", settings.app_env)
    logger.info("  host        : %s:%d", settings.app_host, settings.app_port)
    logger.info("  max_file_mb : %d", settings.max_file_size_mb)
    logger.info("  ocr_threshold: %d%%", settings.ocr_confidence_threshold)
    logger.info("  tmp_dir     : %s", settings.tmp_dir)
    logger.info("=" * 60)

    # Pre-warm pipeline so first request isn't slow
    from backend.app.api.v1.routes import get_pipeline
    get_pipeline()
    logger.info("Pipeline pre-warmed and ready")

    yield  # ← application runs here

    # ── Shutdown ──────────────────────────────────────────────────────────────
    logger.info("SmartFill AI shutting down gracefully")


# ── Application factory ───────────────────────────────────────────────────────

def create_app() -> FastAPI:
    """
    Create and configure the FastAPI application.

    Separated from module-level `app` instantiation so tests can call
    create_app() to get a fresh instance with overridden dependencies.
    """
    settings = get_settings()

    app = FastAPI(
        title="SmartFill AI",
        description=(
            "AI-powered document extraction API for Internet Centers. "
            "Extracts structured data from Aadhaar and PAN cards."
        ),
        version="1.0.0",
        docs_url="/docs" if settings.is_development else None,
        redoc_url="/redoc" if settings.is_development else None,
        lifespan=lifespan,
    )

    # ── CORS ──────────────────────────────────────────────────────────────────
    # In development: allow localhost so the extension can reach the backend
    # In production: restrict to the specific Chrome Extension origin
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )

    # ── Global exception handlers ─────────────────────────────────────────────
    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        """
        Catch-all for any exception that escapes the route handler.
        Returns a structured ErrorResponse instead of a 500 HTML page.
        """
        logger.error(
            "Unhandled exception | path=%s | error=%s: %s",
            request.url.path,
            type(exc).__name__,
            exc,
        )
        body = ErrorResponse(
            error_code=ErrorCode.INTERNAL_ERROR,
            message="An unexpected server error occurred.",
        )
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=body.model_dump(),
        )

    @app.exception_handler(404)
    async def not_found_handler(request: Request, exc) -> JSONResponse:
        body = ErrorResponse(
            error_code="NOT_FOUND",
            message=f"Endpoint not found: {request.url.path}",
        )
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content=body.model_dump(),
        )

    # ── Routers ───────────────────────────────────────────────────────────────
    app.include_router(router)

    return app


# ── Module-level app instance (used by uvicorn) ───────────────────────────────
app = create_app()
