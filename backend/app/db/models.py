"""
db/models.py
------------
SQLAlchemy models.

users        — one row per distinct Google account that has ever signed in.
login_events — one row per login/logout event, used to compute daily
               active user counts on the admin dashboard.

exams             — one row per configured exam/application (e.g. "RRB NTPC 2026").
exam_fields        — the field layout for one exam's application form, each
                      mapped to a CustomerProfile field + optional transform.
exam_photo_specs   — required photo/signature pixel dimensions + file size
                      for one exam (replaces the old hardcoded EXAM_PRESETS
                      object that used to live in upload.html).
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.db.database import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), default="")
    picture_url: Mapped[str] = mapped_column(String(512), default="")
    first_login_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=dt.datetime.utcnow)
    last_login_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=dt.datetime.utcnow)
    total_logins: Mapped[int] = mapped_column(Integer, default=0)

    login_events: Mapped[list["LoginEvent"]] = relationship(back_populates="user")


class LoginEvent(Base):
    __tablename__ = "login_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    email: Mapped[str] = mapped_column(String(255), index=True, nullable=False)  # denormalised for fast queries
    event_type: Mapped[str] = mapped_column(String(20), nullable=False)  # "login" | "logout"
    timestamp: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=dt.datetime.utcnow, index=True)

    user: Mapped["User"] = relationship(back_populates="login_events")


# ── Exam schema configuration ──────────────────────────────────────────────

class Exam(Base):
    __tablename__ = "exams"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)  # e.g. "rrb_ntpc_2026"
    display_name: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)  # e.g. "RRB NTPC 2026"
    category: Mapped[str] = mapped_column(String(64), default="other")  # railway/ssc/banking/upsc/other
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=dt.datetime.utcnow)

    fields: Mapped[list["ExamField"]] = relationship(
        back_populates="exam", cascade="all, delete-orphan", order_by="ExamField.sort_order"
    )
    photo_specs: Mapped[list["ExamPhotoSpec"]] = relationship(
        back_populates="exam", cascade="all, delete-orphan"
    )


class ExamField(Base):
    __tablename__ = "exam_fields"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    exam_id: Mapped[int] = mapped_column(ForeignKey("exams.id"), nullable=False, index=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)

    display_label: Mapped[str] = mapped_column(String(255), nullable=False)       # "First Name"
    field_key: Mapped[str] = mapped_column(String(128), nullable=False)           # "first_name"

    # A field is EITHER derived from extracted data (source_profile_key set,
    # transform applied) OR a fixed constant you set once (default_value set,
    # source_profile_key left empty) — e.g. "Choice of Language" = "English"
    # for every applicant at this centre. Exactly one of these should be set;
    # the admin UI enforces that, this column allows either.
    source_profile_key: Mapped[str] = mapped_column(String(128), nullable=True)   # "name", or NULL for a constant
    default_value: Mapped[str] = mapped_column(String(500), nullable=True)        # "ENGLISH", "NO", etc.

    transform: Mapped[str] = mapped_column(String(64), default="verbatim")        # see TRANSFORMS enum
    field_type: Mapped[str] = mapped_column(String(32), default="text")           # text/date/dropdown
    required: Mapped[bool] = mapped_column(Boolean, default=False)

    exam: Mapped["Exam"] = relationship(back_populates="fields")


class ExamPhotoSpec(Base):
    __tablename__ = "exam_photo_specs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    exam_id: Mapped[int] = mapped_column(ForeignKey("exams.id"), nullable=False, index=True)
    asset_type: Mapped[str] = mapped_column(String(16), nullable=False)  # "photo" | "signature"

    width_px: Mapped[int] = mapped_column(Integer, nullable=True)
    height_px: Mapped[int] = mapped_column(Integer, nullable=True)
    min_kb: Mapped[int] = mapped_column(Integer, nullable=True)
    max_kb: Mapped[int] = mapped_column(Integer, nullable=False, default=50)
    image_format: Mapped[str] = mapped_column(String(8), default="jpg")

    exam: Mapped["Exam"] = relationship(back_populates="photo_specs")
