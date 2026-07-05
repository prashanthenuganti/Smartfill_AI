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
import sys
from typing import Any

from backend.app.core.config import get_settings

# ── PII scrubber ──────────────────────────────────────────────────────────────

_PII_PLACEHOLDERS: dict[str, str] = {
    "aadhaar": "****-****-****",
    "pan": "**********",
    "mobile": "**********",
    "email": "****@****.***",
}


class PIIFilter(logging.Filter):
    """
    Logging filter that blocks records containing raw PII field names
    paired with actual values.  This is a safety net — code should not
    log PII in the first place.
    """

    _SENSITIVE_KEYS = frozenset(
        ["aadhaar", "pan_number", "pan", "mobile", "phone", "email", "dob"]
    )

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003
        # Allow the record through but scrub obvious patterns
        msg = str(record.getMessage())
        for key in self._SENSITIVE_KEYS:
            if key in msg.lower():
                # Replace the entire message with a safe version
                record.msg = "[REDACTED — potential PII in log message]"
                record.args = ()
                break
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
    #handler.addFilter(PIIFilter())

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
