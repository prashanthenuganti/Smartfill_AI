"""
app/main.py
-----------
FastAPI application entry point for Mitra Fill.

Responsibilities:
  - Create the FastAPI app instance
  - Configure CORS (Chrome Extension origin)
  - Configure session cookies (Google login)
  - Register global exception handlers (converts unhandled errors to ErrorResponse)
  - Mount the v1 router, auth router, admin router
  - Run logging configuration + DB init at startup via lifespan
  - Expose `app` for uvicorn

Run locally:
    uvicorn backend.app.main:app --reload --host 127.0.0.1 --port 8000
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.sessions import SessionMiddleware

from backend.app.api.v1.routes import router
from backend.app.api.v1.auth_routes import router as auth_router
from backend.app.api.v1.admin_routes import router as admin_router
from backend.app.api.v1.exam_routes import router as exam_router
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
      - Initialise database tables (users, login_events)
      - Pre-warm the pipeline (loads Tesseract, instantiates parsers)

    Shutdown:
      - Log graceful shutdown message
    """
    # ── Startup ───────────────────────────────────────────────────────────────
    configure_logging()
    settings = get_settings()

    logger.info("=" * 60)
    logger.info("Mitra Fill starting up")
    logger.info("  environment : %s", settings.app_env)
    logger.info("  host        : %s:%d", settings.app_host, settings.app_port)
    logger.info("  max_file_mb : %d", settings.max_file_size_mb)
    logger.info("  ocr_threshold: %d%%", settings.ocr_confidence_threshold)
    logger.info("  tmp_dir     : %s", settings.tmp_dir)
    logger.info("  google_oauth: %s", "configured" if settings.google_oauth_configured else "NOT CONFIGURED")
    logger.info("  admin_emails: %d whitelisted", len(settings.admin_emails))
    logger.info("=" * 60)

    if not settings.google_oauth_configured:
        logger.warning(
            "GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET not set — "
            "login will not work until these are added to .env"
        )

    # Initialise database (creates users / login_events tables if missing)
    from backend.app.db.database import init_db
    init_db()

    # Pre-warm pipeline so first request isn't slow
    from backend.app.api.v1.routes import get_pipeline
    get_pipeline()
    logger.info("Pipeline pre-warmed and ready")

    yield  # ← application runs here

    # ── Shutdown ──────────────────────────────────────────────────────────────
    logger.info("Mitra Fill shutting down gracefully")


# ── Application factory ───────────────────────────────────────────────────────

def create_app() -> FastAPI:
    """
    Create and configure the FastAPI application.

    Separated from module-level `app` instantiation so tests can call
    create_app() to get a fresh instance with overridden dependencies.
    """
    settings = get_settings()

    app = FastAPI(
        title="Mitra Fill",
        description=(
            "AI-powered document extraction API for Internet Centers. "
            "Extracts structured data from Aadhaar and PAN cards."
        ),
        version="1.0.0",
        docs_url="/docs" if settings.is_development else None,
        redoc_url="/redoc" if settings.is_development else None,
        lifespan=lifespan,
    )

    # ── Session cookies (Google login) ───────────────────────────────────────
    # Must be added before routes that read request.session.
    #
    # same_site: the Chrome extension calling the API is a cross-ORIGIN
    # request (chrome-extension://... -> https://your-api), so the
    # session cookie needs SameSite=None to be sent at all — SameSite=Lax
    # (the normal default) is NOT sent on this kind of cross-origin
    # fetch(), which would silently break the extension's ability to
    # know which operator it's fetching data for.
    #
    # BUT: SameSite=None is only honored by browsers when the cookie is
    # also Secure (HTTPS-only) — this is a real browser security rule,
    # not a config choice. That means this only works once deployed to
    # Railway (HTTPS). In local development (http://127.0.0.1), we keep
    # SameSite=Lax — the extension won't be able to identify the operator
    # via cookie locally, but the core web app (upload -> review ->
    # save-session) is unaffected either way, since those are same-origin
    # requests that were never subject to this restriction.
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.session_secret_key,
        session_cookie="smartfill_session",
        max_age=14 * 24 * 60 * 60,  # 14 days
        same_site="lax" if settings.is_development else "none",
        https_only=not settings.is_development,
    )

    # ── CORS ──────────────────────────────────────────────────────────────────
    # allow_credentials=True + DELETE added so the Chrome extension can
    # send the session cookie on get-session/clear-session (needed now
    # that those endpoints are gated per-operator, not a shared global
    # session — see routes.py). allow_origins must list the extension's
    # real chrome-extension://<ID> origin explicitly once you know it
    # (visible in chrome://extensions with Developer Mode on) — a
    # wildcard "*" origin is NOT allowed by browsers when credentials
    # are enabled, so this needs the real ID added to ALLOWED_ORIGINS.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "DELETE"],
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
    app.include_router(auth_router)
    app.include_router(admin_router)
    app.include_router(exam_router)

    return app


# ── Module-level app instance (used by uvicorn) ───────────────────────────────
app = create_app()
