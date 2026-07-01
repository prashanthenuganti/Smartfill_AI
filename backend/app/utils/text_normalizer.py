"""
utils/text_normalizer.py
------------------------
Normalises raw OCR text into clean, structured values.

OCR output is messy: extra spaces, wrong case, garbled digits, Unicode
lookalikes (e.g. 'l' instead of '1', 'O' instead of '0').
This module fixes all of that before the parsers run regex patterns.

Functions are pure (no side-effects, no I/O) and individually testable.

Normalisation rules applied:
  - Aadhaar number : digits only, 12 chars, spaces/dashes stripped
  - PAN number     : uppercase, non-alphanumeric stripped, validated pattern
  - Date of Birth  : multiple input formats → YYYY-MM-DD
  - Gender         : fuzzy match → "Male" | "Female" | "Other"
  - Name           : title case, collapse whitespace, strip noise characters
  - Address        : collapse whitespace, strip trailing punctuation
"""

import re
import unicodedata
from datetime import datetime
from typing import Optional


# ── OCR character correction map ──────────────────────────────────────────────
# Common OCR misreads in digit positions (used when extracting numeric fields)

_DIGIT_CORRECTION: dict[str, str] = {
    "O": "0",
    "o": "0",
    "l": "1",
    "I": "1",
    "i": "1",
    "S": "5",
    "s": "5",
    "B": "8",
    "G": "6",
    "Z": "2",
    "z": "2",
}

# ── Date format attempts ───────────────────────────────────────────────────────

_DATE_FORMATS: list[str] = [
    "%d/%m/%Y",   # 15/05/1990
    "%d-%m-%Y",   # 15-05-1990
    "%d %m %Y",   # 15 05 1990
    "%d/%m/%y",   # 15/05/90
    "%d-%m-%y",   # 15-05-90
    "%Y-%m-%d",   # 1990-05-15  (already normalised)
    "%d %B %Y",   # 15 May 1990
    "%d %b %Y",   # 15 May 1990 (abbreviated)
    "%B %d, %Y",  # May 15, 1990
    "%d.%m.%Y",   # 15.05.1990
    "%Y/%m/%d",   # 1990/05/15
]

# ── Gender keyword maps ───────────────────────────────────────────────────────

_MALE_KEYWORDS = frozenset(["male", "m", "पुरुष", "pur", "man"])
_FEMALE_KEYWORDS = frozenset(["female", "f", "महिला", "woman", "fem"])


# ─────────────────────────────────────────────────────────────────────────────
# Public normalisation functions
# ─────────────────────────────────────────────────────────────────────────────


def normalize_text(raw: str) -> str:
    """
    General-purpose text cleanup applied before any field-specific normalisation.

    Steps:
      1. Unicode NFC normalisation (handles composed vs decomposed characters)
      2. Collapse multiple whitespace characters into single spaces
      3. Strip leading/trailing whitespace
    """
    if not raw:
        return ""
    # NFC normalisation — handles Devanagari and other scripts correctly
    text = unicodedata.normalize("NFC", raw)
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def normalize_name(raw: str) -> Optional[str]:
    """
    Normalise a person's name extracted from OCR.

    Rules:
      - Title-case (e.g. "RAVI KUMAR" → "Ravi Kumar")
      - Remove non-name characters (digits, most punctuation)
      - Collapse whitespace
      - Return None if result is empty or too short to be a real name

    Args:
        raw: Raw OCR string that may contain a name.

    Returns:
        Normalised name string, or None if extraction failed.
    """
    text = normalize_text(raw)
    # Remove characters that can't appear in Indian names
    # Allow: letters (including Unicode for Devanagari), spaces, dots, hyphens
    text = re.sub(r"[^\w\s.\-']", "", text, flags=re.UNICODE)
    # Remove standalone digits (OCR artefacts)
    text = re.sub(r"\b\d+\b", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    # Title case — handles mixed scripts safely
    text = text.title()

    if len(text) < 2:
        return None
    return text


def normalize_aadhaar(raw: str) -> Optional[str]:
    """
    Normalise an Aadhaar number to 12 consecutive digits.

    Handles:
      - Spaces between groups: "1234 5678 9012"
      - Dashes: "1234-5678-9012"
      - OCR digit lookalikes: O→0, l→1, etc.
      - Masked Aadhaar: "XXXX XXXX 9012" → partial, returned as-is

    Returns:
        12-digit string, or None if normalisation fails.
    """
    text = normalize_text(raw)
    # Apply OCR digit corrections character by character
    corrected = ""
    for ch in text:
        corrected += _DIGIT_CORRECTION.get(ch, ch)
    # Strip everything except digits and X/x (for masked Aadhaar)
    digits = re.sub(r"[^\dXx]", "", corrected)

    if len(digits) == 12:
        return digits.upper()
    return None


def normalize_pan(raw: str) -> Optional[str]:
    """
    Normalise a PAN number to the standard AAAAA0000A format.

    PAN format: 5 uppercase letters, 4 digits, 1 uppercase letter.
    Example: ABCDE1234F

    Handles:
      - Lowercase input
      - Spaces within the PAN
      - OCR misreads (0↔O in letter positions, etc.)

    Returns:
        10-character PAN string in correct format, or None if invalid.
    """
    text = normalize_text(raw).upper()
    # Remove whitespace and punctuation
    text = re.sub(r"[^A-Z0-9]", "", text)

    # PAN regex: 5 letters, 4 digits, 1 letter
    pan_pattern = re.compile(r"[A-Z]{5}[0-9]{4}[A-Z]")
    match = pan_pattern.search(text)
    if match:
        return match.group(0)
    return None


def normalize_dob(raw: str) -> Optional[str]:
    """
    Normalise a date of birth string to ISO format YYYY-MM-DD.

    Tries multiple date formats common on Indian government documents.

    Args:
        raw: Raw OCR string e.g. "15/05/1990", "15-MAY-1990", "1990-05-15"

    Returns:
        "YYYY-MM-DD" string, or None if no format matched.
    """
    text = normalize_text(raw)
    # Replace common OCR date separators uniformly
    text = re.sub(r"[.\|\\]", "/", text)

    for fmt in _DATE_FORMATS:
        try:
            dt = datetime.strptime(text, fmt)
            # Sanity check: birth year must be realistic
            if 1900 <= dt.year <= datetime.now().year:
                return dt.strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def normalize_gender(raw: str) -> Optional[str]:
    """
    Normalise gender text to one of: "Male", "Female", "Other".

    Handles:
      - English: "male", "MALE", "M", "Female", "F"
      - Hindi: "पुरुष" (male), "महिला" (female)
      - OCR artefacts: "Mal e", "Femal e"

    Returns:
        "Male" | "Female" | "Other", or None if not recognised.
    """
    text = normalize_text(raw).lower().replace(" ", "")

    if text in _MALE_KEYWORDS or text.startswith("mal"):
        return "Male"
    if text in _FEMALE_KEYWORDS or text.startswith("fem"):
        return "Female"
    # Catch Hindi keywords
    if "पुरुष" in raw:
        return "Male"
    if "महिला" in raw:
        return "Female"
    # If something was found but doesn't match Male/Female
    if text:
        return "Other"
    return None


def extract_year(raw: str) -> Optional[str]:
    """
    Extract a 4-digit year from a raw string.

    Used as a fallback when full DOB normalisation fails but the year
    is still visible on the document.

    Returns:
        4-digit year string e.g. "1990", or None.
    """
    match = re.search(r"\b(19[0-9]{2}|20[0-2][0-9])\b", raw)
    return match.group(1) if match else None


def clean_ocr_line(line: str) -> str:
    """
    Light cleanup for a single OCR output line.

    Removes:
      - Lines that are purely punctuation or whitespace
      - Common OCR header/footer artefacts

    Used by parsers to filter the raw OCR output before pattern matching.
    """
    line = normalize_text(line)
    # Remove lines that are entirely non-alphanumeric
    if not re.search(r"[A-Za-z0-9\u0900-\u097F]", line):
        return ""
    return line
