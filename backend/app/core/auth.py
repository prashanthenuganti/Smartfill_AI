"""
core/auth.py
------------
Google OAuth login (via Authlib) + session-based auth dependencies.

Flow:
  1. GET /auth/login       → redirect to Google's consent screen
  2. GET /auth/callback    → Google redirects back with a code;
                             we exchange it for the user's profile,
                             upsert a User row, log a LoginEvent,
                             and store {"email": ...} in the session cookie.
  3. Protected routes use `Depends(require_login)` — reads the session
     cookie, 401s (API) or redirects to /auth/login (HTML pages) if absent.
  4. GET /auth/logout      → logs a LoginEvent("logout"), clears the session.

Session storage: signed cookies via Starlette's SessionMiddleware
(added in main.py). Nothing server-side to manage — the cookie itself
holds the (signed, tamper-proof) session data.
"""

from __future__ import annotations

from authlib.integrations.starlette_client import OAuth
from fastapi import Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse

from backend.app.core.config import get_settings
from backend.app.core.logging import get_logger

logger = get_logger(__name__)
settings = get_settings()

oauth = OAuth()
oauth.register(
    name="google",
    client_id=settings.google_client_id,
    client_secret=settings.google_client_secret,
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    client_kwargs={"scope": "openid email profile"},
)


def get_current_user_email(request: Request) -> str | None:
    """Return the logged-in user's email from the session cookie, or None."""
    return request.session.get("email")


def require_login(request: Request) -> str:
    """
    FastAPI dependency for API endpoints.
    Raises 401 JSON error if not logged in. Returns the user's email if logged in.
    """
    email = get_current_user_email(request)
    if not email:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not logged in. Please sign in with Google first.",
        )
    return email


def redirect_if_not_logged_in(request: Request) -> RedirectResponse | None:
    """
    Helper for HTML page routes (upload page, review page) — NOT used as a
    FastAPI Depends() (dependencies can't swap response types). Instead,
    call this directly at the top of the route function:

        @router.get("/app")
        async def upload_page(request: Request):
            redirect = redirect_if_not_logged_in(request)
            if redirect:
                return redirect
            ...

    Returns a RedirectResponse to /auth/login if not logged in, else None.
    """
    if not get_current_user_email(request):
        request.session["next_url"] = str(request.url)
        return RedirectResponse(url="/login")
    return None


def require_admin(request: Request) -> str:
    """
    FastAPI dependency for the admin dashboard.
    Requires login AND email must be in settings.admin_emails.
    """
    email = require_login(request)
    if email.lower() not in [e.lower() for e in settings.admin_emails]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have access to the admin dashboard.",
        )
    return email
