"""
api/v1/auth_routes.py
----------------------
Google OAuth login flow + logout, with login/logout events logged to
Postgres for the admin dashboard's daily-active-user counts.
"""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from backend.app.core.auth import get_current_user_email, oauth, require_login
from backend.app.core.config import get_settings
from backend.app.core.logging import get_logger
from backend.app.db.database import get_db
from backend.app.db.models import LoginEvent, User

logger = get_logger(__name__)
router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/login")
async def login(request: Request):
    """Redirect to Google's OAuth consent screen."""
    settings = get_settings()
    if settings.public_base_url:
        # Explicit, not inferred — see the comment on public_base_url in
        # config.py for why this is preferred over request.url_for()
        # behind Railway's reverse proxy.
        redirect_uri = f"{settings.public_base_url.rstrip('/')}/auth/callback"
    else:
        redirect_uri = request.url_for("auth_callback")
    return await oauth.google.authorize_redirect(request, redirect_uri)


@router.get("/callback", name="auth_callback")
async def auth_callback(request: Request, db: Session = Depends(get_db)):
    """
    Google redirects here after consent. Exchange the code for tokens,
    read the user's profile, upsert the User row, log a LoginEvent,
    and store their email in the session cookie.
    """
    try:
        token = await oauth.google.authorize_access_token(request)
    except Exception as exc:
        logger.error("OAuth callback failed | %s", exc)
        return HTMLResponse(
            "<h2>Login failed.</h2><p>Please try again.</p><a href='/auth/login'>Retry</a>",
            status_code=400,
        )

    userinfo = token.get("userinfo") or {}
    email = userinfo.get("email")
    name = userinfo.get("name", "")
    picture = userinfo.get("picture", "")

    if not email:
        return HTMLResponse("<h2>Login failed.</h2><p>No email returned by Google.</p>", status_code=400)

    # Upsert user
    user = db.query(User).filter(User.email == email).first()
    now = dt.datetime.utcnow()
    if user is None:
        user = User(
            email=email, name=name, picture_url=picture,
            first_login_at=now, last_login_at=now, total_logins=1,
        )
        db.add(user)
        logger.info("New user registered | email=%s", email)
    else:
        user.last_login_at = now
        user.total_logins += 1
        user.name = name or user.name
        user.picture_url = picture or user.picture_url

    db.commit()
    db.refresh(user)

    db.add(LoginEvent(user_id=user.id, email=email, event_type="login", timestamp=now))
    db.commit()

    request.session["email"] = email
    request.session["name"] = name

    logger.info("User logged in | email=%s", email)

    # Send them to the app they were trying to reach, default to /app
    next_url = request.session.pop("next_url", None) or "/app"
    return RedirectResponse(url=next_url)


@router.get("/logout")
async def logout(request: Request, db: Session = Depends(get_db)):
    """Log a logout event and clear the session cookie."""
    email = get_current_user_email(request)
    if email:
        user = db.query(User).filter(User.email == email).first()
        if user:
            db.add(LoginEvent(user_id=user.id, email=email, event_type="logout", timestamp=dt.datetime.utcnow()))
            db.commit()
        logger.info("User logged out | email=%s", email)

    request.session.clear()
    return RedirectResponse(url="/auth/login")


@router.get("/me")
async def me(request: Request, email: str = Depends(require_login)):
    """
    JSON endpoint the shared nav sidebar calls on every page load to know
    who's logged in, what to display, and whether to show admin links.
    """
    settings = get_settings()
    name = request.session.get("name") or ""
    is_admin = email.lower() in [e.lower() for e in settings.admin_emails]
    return {"email": email, "name": name, "is_admin": is_admin}
