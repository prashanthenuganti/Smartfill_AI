"""
core/exceptions.py
------------------
All custom exception types for SmartFill AI.

Defining exceptions here (rather than inside individual modules) prevents
circular imports and gives a single place to audit error taxonomy.
"""


class SmartFillBaseError(Exception):
    """Root exception for all application-specific errors."""

    def __init__(self, message: str, details: dict | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


# ── File / Upload errors ──────────────────────────────────────────────────────

class FileTooLargeError(SmartFillBaseError):
    """Uploaded file exceeds the configured size limit."""


class UnsupportedFileTypeError(SmartFillBaseError):
    """File extension is not in the allowed list."""


class CorruptedFileError(SmartFillBaseError):
    """File cannot be opened or decoded."""


# ── Pipeline errors ───────────────────────────────────────────────────────────

class PreprocessingError(SmartFillBaseError):
    """Image preprocessing step failed."""


class OCRError(SmartFillBaseError):
    """OCR engine returned an error or empty result."""


class LowConfidenceError(SmartFillBaseError):
    """OCR confidence fell below threshold even after retry."""


# ── Parser errors ─────────────────────────────────────────────────────────────

class ParserError(SmartFillBaseError):
    """Document parser could not extract required fields."""


class ValidationError(SmartFillBaseError):
    """Extracted data failed format/regex validation."""
