"""
api/v1/admin_routes.py
------------------------
Admin-only dashboard: daily active user counts, total users, recent
login/logout activity. Gated by require_admin (email allowlist in .env).
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.app.core.auth import require_admin
from backend.app.db.database import get_db
from backend.app.db.models import LoginEvent, User

router = APIRouter(tags=["admin"])


@router.get("/admin/dashboard", response_class=HTMLResponse, include_in_schema=False)
async def admin_dashboard_page(request: Request, _email: str = Depends(require_admin)):
    html_path = Path(__file__).parents[4] / "frontend" / "admin_dashboard.html"
    if html_path.exists():
        return HTMLResponse(content=html_path.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>admin_dashboard.html not found</h1>", status_code=404)


@router.get("/admin/exams", response_class=HTMLResponse, include_in_schema=False)
async def admin_exams_page(request: Request, _email: str = Depends(require_admin)):
    html_path = Path(__file__).parents[4] / "frontend" / "admin_exams.html"
    if html_path.exists():
        return HTMLResponse(content=html_path.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>admin_exams.html not found</h1>", status_code=404)


@router.get("/api/v1/admin/stats")
async def admin_stats(
    db: Session = Depends(get_db),
    _email: str = Depends(require_admin),
) -> JSONResponse:
    """
    Returns:
      total_users        — all-time distinct users ever logged in
      today_logins        — distinct users who logged in today
      daily_active_users  — list of {date, count} for the last 30 days
      recent_events       — last 25 login/logout events
    """
    today = dt.datetime.utcnow().date()
    thirty_days_ago = today - dt.timedelta(days=29)

    total_users = db.query(func.count(User.id)).scalar() or 0

    # Distinct users who logged in today
    today_logins = (
        db.query(func.count(func.distinct(LoginEvent.email)))
        .filter(
            LoginEvent.event_type == "login",
            func.date(LoginEvent.timestamp) == today,
        )
        .scalar()
        or 0
    )

    # Daily active users for the last 30 days
    daily_rows = (
        db.query(
            func.date(LoginEvent.timestamp).label("day"),
            func.count(func.distinct(LoginEvent.email)).label("count"),
        )
        .filter(
            LoginEvent.event_type == "login",
            func.date(LoginEvent.timestamp) >= thirty_days_ago,
        )
        .group_by(func.date(LoginEvent.timestamp))
        .order_by(func.date(LoginEvent.timestamp))
        .all()
    )
    # Build a complete 30-day series (fill in zero-days that had no logins)
    by_day = {str(row.day): row.count for row in daily_rows}
    daily_active_users = []
    for i in range(30):
        d = thirty_days_ago + dt.timedelta(days=i)
        daily_active_users.append({"date": str(d), "count": by_day.get(str(d), 0)})

    # Recent activity feed
    recent = (
        db.query(LoginEvent)
        .order_by(LoginEvent.timestamp.desc())
        .limit(25)
        .all()
    )
    recent_events = [
        {
            "email": e.email,
            "event_type": e.event_type,
            "timestamp": e.timestamp.isoformat(),
        }
        for e in recent
    ]

    return JSONResponse({
        "total_users": total_users,
        "today_logins": today_logins,
        "daily_active_users": daily_active_users,
        "recent_events": recent_events,
    })
