"""
schemas/errors.py
-----------------
Standardised error response body.

Every error the API returns — validation failure, OCR error, file too large —
uses this shape so the Chrome Extension has a single error-handling path.

Shape:
    {
        "status": "error",
        "error_code": "FILE_TOO_LARGE",
        "message": "Uploaded file exceeds the 10 MB limit.",
        "details": {}
    }
"""

from typing import Any, Optional
from pydantic import BaseModel


class ErrorResponse(BaseModel):
    """Standard error envelope returned for all 4xx and 5xx responses."""

    status: str = "error"
    error_code: str
    message: str
    details: dict[str, Any] = {}


# ── Error code constants ──────────────────────────────────────────────────────
# Keeping these as module-level strings (not an Enum) so they can be used
# directly in FastAPI HTTPException detail dicts without serialisation overhead.

class ErrorCode:
    # File errors
    FILE_TOO_LARGE = "FILE_TOO_LARGE"
    UNSUPPORTED_FILE_TYPE = "UNSUPPORTED_FILE_TYPE"
    CORRUPTED_FILE = "CORRUPTED_FILE"
    NO_FILE_PROVIDED = "NO_FILE_PROVIDED"

    # Pipeline errors
    PREPROCESSING_FAILED = "PREPROCESSING_FAILED"
    OCR_FAILED = "OCR_FAILED"
    LOW_CONFIDENCE = "LOW_CONFIDENCE"

    # Parser errors
    PARSE_FAILED = "PARSE_FAILED"
    VALIDATION_FAILED = "VALIDATION_FAILED"

    # Server errors
    INTERNAL_ERROR = "INTERNAL_ERROR"
    SERVICE_UNAVAILABLE = "SERVICE_UNAVAILABLE"
