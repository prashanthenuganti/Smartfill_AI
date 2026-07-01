"""
core/config.py
--------------
Centralised application configuration for Milestone 2.

New settings added:
  - ANTHROPIC_API_KEY       : for AI extraction layer
  - SURYA_ENABLED           : toggle Surya OCR tier 2
  - AI_EXTRACTION_ENABLED   : toggle Haiku AI extraction
  - AI_MODEL                : which Anthropic model to use
  - OCR_TIER2_CONFIDENCE    : threshold to escalate to Surya
  - OCR_TIER3_CONFIDENCE    : threshold to escalate to AI vision
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
    # Confidence below which Tesseract result is sent to Surya
    ocr_tier2_confidence: int = Field(default=60, ge=0, le=100)
    # Surya device: "cpu" or "cuda" (auto-detected if not set)
    surya_device: Optional[str] = Field(default=None)

    # ── Vision Engine ────────────────────────────────────────────────────────
    # "claude" = testing with Haiku 4.5 (uses ANTHROPIC_API_KEY)
    # "gemini" = production with Gemini 2.5 Flash (uses GEMINI_API_KEY)
    vision_engine: str = Field(default="claude")

    # ── Claude (Haiku 4.5 — testing) ─────────────────────────────────────────
    anthropic_api_key: Optional[str] = Field(default=None)
    ai_extraction_enabled: bool = Field(default=True)
    ai_model: str = Field(default="claude-haiku-4-5")
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
