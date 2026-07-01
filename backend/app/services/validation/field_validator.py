"""
services/validation/field_validator.py
----------------------------------------
Validation layer — runs after Vision LLM extraction, before the merger.

Rules:
- Format failures → lower confidence + needs_review=True (amber in UI)
- Values are NEVER discarded — operator sees and corrects
- Normalises values on pass (PAN uppercase, Aadhaar digits-only, etc.)
"""
from __future__ import annotations
import re
from datetime import date, datetime
from backend.app.core.logging import get_logger
from backend.app.schemas.extraction import ExtractionField

logger = get_logger(__name__)

# ── Individual validators ─────────────────────────────────────────────────────

def _pan(v):
    c = re.sub(r"\s+","",v).upper()
    return (True,c) if re.match(r"^[A-Z]{5}[0-9]{4}[A-Z]$",c) else (False,v)

def _aadhaar(v):
    c = re.sub(r"[\s\-]","",v)
    return (True,c) if re.match(r"^\d{12}$",c) else (False,v)

def _date(v):
    if not re.match(r"^\d{4}-\d{2}-\d{2}$",v): return (False,v)
    try:
        d = datetime.strptime(v,"%Y-%m-%d").date()
        return (True,v) if date(1900,1,1)<=d<=date.today() else (False,v)
    except ValueError:
        return (False,v)

def _passport(v):
    c = re.sub(r"\s+","",v).upper()
    return (True,c) if re.match(r"^[A-Z]\d{7}$",c) else (False,v)

def _pincode(v):
    c = re.sub(r"\D","",v)
    return (True,c) if re.match(r"^[1-9]\d{5}$",c) else (False,v)

def _mobile(v):
    c = re.sub(r"\D","",v)
    if c.startswith("91") and len(c)==12: c=c[2:]
    return (True,c) if re.match(r"^[6-9]\d{9}$",c) else (False,v)

def _ifsc(v):
    c = re.sub(r"\s+","",v).upper()
    return (True,c) if re.match(r"^[A-Z]{4}0[A-Z0-9]{6}$",c) else (False,v)

def _email(v):
    c = v.strip().lower()
    return (True,c) if re.match(r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$",c) else (False,v)

def _voter(v):
    c = re.sub(r"\s+","",v).upper()
    return (True,c) if re.match(r"^[A-Z]{3}\d{7}$",c) else (False,v)

def _pct(v):
    c = re.sub(r"[%\s]","",v)
    try:
        p=float(c)
        return (True,f"{p}%") if 0<=p<=100 else (False,v)
    except ValueError:
        return (False,v)

def _dl(v):
    c = re.sub(r"\s+","",v).upper()
    return (True,c) if len(c)>=8 else (False,v)

# ── Field → validator + penalty ───────────────────────────────────────────────

_RULES = {
    "pan_number":       (_pan,      30),
    "aadhaar_number":   (_aadhaar,  25),
    "dob":              (_date,     20),
    "doi":              (_date,     15),
    "doe":              (_date,     15),
    "passport_number":  (_passport, 25),
    "address_pincode":  (_pincode,  20),
    "mobile":           (_mobile,   20),
    "ifsc":             (_ifsc,     20),
    "email":            (_email,    15),
    "voter_id":         (_voter,    20),
    "dl_number":        (_dl,       10),
    "ssc_percentage":   (_pct,      10),
    "inter_percentage": (_pct,      10),
    "percentage":       (_pct,      10),
}

# ── Public API ────────────────────────────────────────────────────────────────

def validate_field(name: str, ef: ExtractionField) -> ExtractionField:
    """Validate one field. Returns normalised field with updated confidence."""
    if not ef.value:
        return ef
    rule = _RULES.get(name)
    if not rule:
        return ef
    validator, penalty = rule
    is_valid, normalised = validator(ef.value)
    if is_valid:
        return ExtractionField(
            value=normalised,
            confidence=min(ef.confidence+3, 100.0),
            needs_review=ef.needs_review,
        )
    else:
        new_conf = max(ef.confidence - penalty, 0.0)
        logger.warning("Validation fail | %s=%r | %.0f→%.0f", name, ef.value, ef.confidence, new_conf)
        return ExtractionField(
            value=ef.value,
            confidence=new_conf,
            needs_review=True,
        )


def validate_all(fields: dict[str, ExtractionField]) -> dict[str, ExtractionField]:
    """Validate all fields in an extraction dict."""
    return {k: validate_field(k, v) for k, v in fields.items()}
