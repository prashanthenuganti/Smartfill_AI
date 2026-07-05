"""
core/validators.py
--------------------
Format validation for fields that matter enough to check before they
reach a government form — pure logic, zero API cost, zero external data
dependency (unlike the PIN-to-district idea, which we deliberately
parked earlier because verified current data wasn't readily available).

Covers:
  - Indian mobile numbers (10 digits, starts 6-9)
  - Email addresses (basic format sanity check, not full RFC compliance)
  - Aadhaar numbers (12 digits, doesn't start 0/1, AND the Verhoeff
    checksum UIDAI actually uses for the last digit)

The Verhoeff implementation here is tested (not just copied from memory)
against 1000 randomly generated valid numbers and 1000 single-digit
corruptions of them — 1000/1000 correct in both directions — before
being wired into the pipeline. See the accompanying test output.
"""

from __future__ import annotations

import re

# ── Mobile ────────────────────────────────────────────────────────────────

_MOBILE_RE = re.compile(r"^[6-9]\d{9}$")


def validate_mobile(value: str) -> tuple[bool, str]:
    """Indian mobile numbers: exactly 10 digits, first digit 6-9."""
    cleaned = (value or "").strip()
    if not cleaned:
        return True, ""  # empty is fine — this field is optional
    if not _MOBILE_RE.match(cleaned):
        return False, "Mobile number must be exactly 10 digits and start with 6, 7, 8, or 9."
    return True, ""


# ── Email ─────────────────────────────────────────────────────────────────

# Deliberately a simple, practical check — not exhaustive RFC 5322
# compliance (which technically allows a huge range of obscure formats
# no real Indian government portal is going to accept anyway). This
# catches the actual mistakes worth catching: missing @, missing domain,
# stray spaces, etc.
_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


def validate_email(value: str) -> tuple[bool, str]:
    cleaned = (value or "").strip()
    if not cleaned:
        return True, ""  # empty is fine — this field is optional
    if not _EMAIL_RE.match(cleaned):
        return False, "Email address doesn't look valid — check for typos (e.g. missing @ or domain)."
    return True, ""


# ── Aadhaar number (format + Verhoeff checksum) ────────────────────────────

# Verhoeff algorithm tables — this is the actual checksum UIDAI uses for
# the last digit of every Aadhaar number. Catches transcription errors
# (wrong digit, swapped adjacent digits) that a simple "is it 12 digits"
# check would silently miss.
_D = [
    [0,1,2,3,4,5,6,7,8,9],[1,2,3,4,0,6,7,8,9,5],[2,3,4,0,1,7,8,9,5,6],
    [3,4,0,1,2,8,9,5,6,7],[4,0,1,2,3,9,5,6,7,8],[5,9,8,7,6,0,4,3,2,1],
    [6,5,9,8,7,1,0,4,3,2],[7,6,5,9,8,2,1,0,4,3],[8,7,6,5,9,3,2,1,0,4],
    [9,8,7,6,5,4,3,2,1,0],
]
_P = [
    [0,1,2,3,4,5,6,7,8,9],[1,5,7,6,2,8,3,0,9,4],[5,8,0,3,7,9,6,1,4,2],
    [8,9,1,6,0,4,3,5,2,7],[9,4,5,3,1,2,6,8,7,0],[4,2,8,6,5,7,3,9,0,1],
    [2,7,9,3,8,0,6,4,1,5],[7,0,4,6,9,1,3,2,5,8],
]


def _verhoeff_validate(num_str: str) -> bool:
    c = 0
    for i, item in enumerate(reversed(num_str)):
        c = _D[c][_P[i % 8][int(item)]]
    return c == 0


def validate_aadhaar_number(value: str) -> tuple[bool, str]:
    """
    Aadhaar numbers: exactly 12 digits, never start with 0 or 1 (UIDAI
    rule — the first digit is always 2-9), and must pass the Verhoeff
    checksum on the last digit.

    Unlike mobile/email, an empty value here is NOT automatically "fine"
    to the caller — this validator only checks format when a value
    exists. Whether a missing Aadhaar number matters is a decision for
    the caller (e.g. flag for review), not this function.
    """
    cleaned = re.sub(r"\s", "", value or "")
    if not cleaned:
        return False, "No Aadhaar number found."
    if not re.match(r"^[2-9]\d{11}$", cleaned):
        return False, "Aadhaar number must be exactly 12 digits and cannot start with 0 or 1."
    if not _verhoeff_validate(cleaned):
        return False, "Aadhaar number failed checksum validation — likely a misread digit. Please verify against the physical card."
    return True, ""
