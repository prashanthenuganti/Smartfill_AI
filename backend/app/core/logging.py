"""
core/logging.py
---------------
Centralised logging configuration for SmartFill AI.

Rules enforced here:
  - Aadhaar numbers, PAN numbers, and personal info are NEVER logged.
  - Log level is DEBUG in development, INFO in production.
  - Format is structured and human-readable for easy grep.
"""

import logging
import re
import sys
from typing import Any

from backend.app.core.config import get_settings

# ── PII scrubber ──────────────────────────────────────────────────────────────
#
# This matches PII by VALUE SHAPE (regex patterns for what an actual
# Aadhaar/mobile/PAN/email/VID number looks like), not by field NAME.
#
# An earlier version matched on field names instead (blocking any message
# containing the word "aadhaar", "mobile", "dob", etc.) — that had to be
# disabled because it was blocking completely benign messages too, e.g.
# "type=aadhaar" or "document_type=aadhaar" (just naming which document
# type was being processed, no actual PII value present) — hiding useful
# debugging info along with whatever PII it was meant to catch. Matching
# the actual value shape instead means only the real sensitive substring
# gets redacted; the rest of the message stays intact and useful.

# Order matters: longer/more-specific digit patterns are checked before
# shorter ones, so e.g. a 12-digit Aadhaar number's own digits can't be
# partially re-matched by the shorter mobile-number pattern afterward.
_VID_RE = re.compile(r"\b\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}\b")       # 16-digit VID
_AADHAAR_RE = re.compile(r"\b\d{4}[\s-]?\d{4}[\s-]?\d{4}\b")             # 12-digit Aadhaar
_MOBILE_RE = re.compile(r"\b[6-9]\d{9}\b")                               # 10-digit Indian mobile
_PAN_RE = re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b")                          # PAN: AAAAA0000A
_EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")


class PIIFilter(logging.Filter):
    """
    Redacts actual PII values found in a log message, based on their
    shape — never blocks or replaces an entire message just because it
    mentions a sensitive-sounding field name.
    """

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003
        msg = str(record.getMessage())
        redacted = _VID_RE.sub("[VID-REDACTED]", msg)
        redacted = _AADHAAR_RE.sub("[AADHAAR-REDACTED]", redacted)
        redacted = _MOBILE_RE.sub("[MOBILE-REDACTED]", redacted)
        redacted = _PAN_RE.sub("[PAN-REDACTED]", redacted)
        redacted = _EMAIL_RE.sub("[EMAIL-REDACTED]", redacted)

        if redacted != msg:
            record.msg = redacted
            record.args = ()
        return True


# ── Formatter ─────────────────────────────────────────────────────────────────

_LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def configure_logging() -> None:
    """
    Call once at application startup (inside main.py lifespan).
    Configures the root logger and attaches the PII filter.
    """
    settings = get_settings()
    level = logging.DEBUG if settings.is_development else logging.INFO

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FORMAT))
    handler.addFilter(PIIFilter())

    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()
    root.addHandler(handler)

    # Silence noisy third-party loggers
    logging.getLogger("multipart").setLevel(logging.WARNING)
    logging.getLogger("PIL").setLevel(logging.WARNING)
    logging.getLogger("fitz").setLevel(logging.WARNING)

    # These three are what flood the terminal with raw HTTP request/response
    # dumps (including base64 image data) whenever root is at DEBUG level —
    # every Claude Vision API call was printing its full payload. Silencing
    # them keeps your own app's DEBUG logs intact while cutting the noise.
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("anthropic").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """
    Factory used by every module instead of logging.getLogger(__name__).

    Usage:
        from backend.app.core.logging import get_logger
        logger = get_logger(__name__)
    """
    return logging.getLogger(name)
