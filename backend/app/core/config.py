"""
core/config.py
--------------
Centralised application configuration.

New settings added for Google login + admin dashboard:
  - GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET : OAuth credentials from
    Google Cloud Console (APIs & Services → Credentials)
  - SESSION_SECRET_KEY : random string used to sign session cookies.
    Generate one with: python -c "import secrets; print(secrets.token_hex(32))"
  - ADMIN_EMAILS       : comma-separated list of emails allowed to view
    the /admin/dashboard page
  - DATABASE_URL       : Postgres connection string. On Railway, add a
    Postgres plugin and this is auto-injected — no manual .env edit needed
    there. Locally, falls back to a SQLite file if not set.
"""

from functools import lru_cache
from pathlib import Path
from typing import List, Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Server ────────────────────────────────────────────────────────────────
    app_host: str = Field(default="127.0.0.1")
    app_port: int = Field(default=8000)
    app_env: str = Field(default="development")

    # ── OCR Tier 1: Tesseract ─────────────────────────────────────────────────
    tesseract_cmd: str = Field(default="/usr/bin/tesseract")
    ocr_confidence_threshold: int = Field(default=60, ge=0, le=100)
    ocr_timeout_seconds: int = Field(default=30, ge=5)

    # ── OCR Tier 2: Surya ─────────────────────────────────────────────────────
    surya_enabled: bool = Field(default=True)
    ocr_tier2_confidence: int = Field(default=60, ge=0, le=100)
    surya_device: Optional[str] = Field(default=None)

    # ── Vision Engine ────────────────────────────────────────────────────────
    vision_engine: str = Field(default="claude")

    # ── Claude (Haiku 4.5) ────────────────────────────────────────────────────
    anthropic_api_key: Optional[str] = Field(default=None)
    ai_extraction_enabled: bool = Field(default=True)
    ai_model: str = Field(default="claude-haiku-4-5-20251001")
    ai_max_tokens: int = Field(default=1024)
    ocr_tier3_confidence: int = Field(default=50, ge=0, le=100)

    # ── Gemini (2.5 Flash — production) ──────────────────────────────────────
    gemini_api_key: Optional[str] = Field(default=None)

    @property
    def has_gemini_key(self) -> bool:
        return bool(self.gemini_api_key)

    # ── File Handling ─────────────────────────────────────────────────────────
    max_file_size_mb: int = Field(default=10, ge=1, le=50)
    allowed_extensions: List[str] = Field(
        default=["pdf", "png", "jpg", "jpeg"]
    )
    tmp_dir: Path = Field(default=Path("/tmp/smartfill"))

    # ── CORS ──────────────────────────────────────────────────────────────────
    allowed_origins: List[str] = Field(default=["http://localhost:3000"])

    # ── Google OAuth Login ───────────────────────────────────────────────────
    google_client_id: Optional[str] = Field(default=None)
    # Set this explicitly on Railway (e.g. https://your-app.up.railway.app)
    # to build the OAuth redirect URI directly, rather than inferring the
    # scheme from the incoming request. Behind Railway's reverse proxy,
    # relying on request.url_for() to correctly detect "https" depends on
    # uvicorn correctly trusting and parsing Railway's forwarded-proto
    # header — a chain with several possible failure points that are hard
    # to verify from outside the container. Setting this removes that
    # uncertainty entirely. Leave unset for local dev — the dynamic
    # behavior works fine there since there's no proxy in front of it.
    public_base_url: Optional[str] = Field(default=None)
    google_client_secret: Optional[str] = Field(default=None)
    session_secret_key: str = Field(default="dev-insecure-change-me-in-env")

    # Plain string on purpose — NOT List[str]. pydantic-settings tries to
    # JSON-decode any List-typed env var before validators even run, which
    # crashes on a plain comma-separated value like "a@x.com,b@x.com".
    # Use the admin_emails property below to get the parsed list.
    admin_emails_raw: str = Field(default="", alias="ADMIN_EMAILS")

    @property
    def admin_emails(self) -> List[str]:
        return [e.strip() for e in self.admin_emails_raw.split(",") if e.strip()]

    @property
    def google_oauth_configured(self) -> bool:
        return bool(self.google_client_id and self.google_client_secret)

    # ── Database (Postgres via Railway, or local SQLite fallback) ────────────
    database_url: str = Field(default="sqlite:///./smartfill_local.db")

    # ── Derived ───────────────────────────────────────────────────────────────
    @property
    def max_file_size_bytes(self) -> int:
        return self.max_file_size_mb * 1024 * 1024

    @property
    def is_development(self) -> bool:
        return self.app_env == "development"

    @property
    def has_anthropic_key(self) -> bool:
        return bool(self.anthropic_api_key)

    @field_validator("tmp_dir", mode="before")
    @classmethod
    def resolve_tmp_dir(cls, v: str | Path) -> Path:
        path = Path(v)
        path.mkdir(parents=True, exist_ok=True)
        return path


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
