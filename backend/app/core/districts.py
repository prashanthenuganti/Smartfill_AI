"""
core/districts.py
--------------------
Validates/normalises extracted district names against a known canonical
list — currently Telangana only, matching SmartFill AI's initial CSC/
MeeSeva target market.

Why this exists: Vision LLM extraction occasionally returns a district
name with an OCR-style typo or minor garbling (e.g. "Khamam" instead of
"Khammam", "Warrangal" instead of "Warangal"). Since district is often a
required field on government application forms, a wrong district name
can mean a rejected or misrouted application — this catches that before
the operator submits, at zero API cost (pure string matching, no LLM call).

Design principles (deliberately conservative — see normalize_district):
  - Exact match (case/whitespace insensitive) → silently use canonical
    casing, no review flag needed (this isn't a "correction", just
    normalising e.g. "khammam" → "Khammam").
  - Fuzzy match (genuine typo/OCR garbling) → correct it, but ALWAYS flag
    for operator review (needs_review=True) rather than silently
    substituting — the operator sees a warning icon on the review page
    and can confirm or override before submitting.
  - No confident match at all → leave the original value untouched and
    flag for review. Do NOT force it into the "closest" district name —
    a customer could genuinely be from outside Telangana, or the value
    could be too garbled to correct reliably. Guessing wrong here is
    worse than leaving it for a human to check.
  - If address_state is present and clearly NOT Telangana, skip
    correction entirely — don't misapply Telangana-specific validation
    to a district in a different state just because the name happens to
    fuzzy-match.
"""

from __future__ import annotations

import difflib
from typing import Optional

TELANGANA_DISTRICTS: list[str] = [
    "Adilabad", "Bhadradri Kothagudem", "Hanumakonda", "Hyderabad", "Jagtial",
    "Jangaon", "Jayashankar Bhupalpally", "Jogulamba Gadwal", "Kamareddy",
    "Karimnagar", "Khammam", "Kumuram Bheem Asifabad", "Mahabubabad",
    "Mahabubnagar", "Mancherial", "Medak", "Medchal-Malkajgiri", "Mulugu",
    "Nagarkurnool", "Nalgonda", "Narayanpet", "Nirmal", "Nizamabad",
    "Peddapalli", "Rajanna Sircilla", "Rangareddy", "Sangareddy", "Siddipet",
    "Suryapet", "Vikarabad", "Wanaparthy", "Warangal", "Yadadri Bhuvanagiri",
]

_LOOKUP: dict[str, str] = {d.casefold(): d for d in TELANGANA_DISTRICTS}

# Fuzzy-match cutoff — deliberately conservative. Higher = fewer false
# corrections but also fewer real typos caught. 0.75 catches things like
# single-character OCR slips without confidently "correcting" a name
# that's only loosely similar (which risks picking the wrong district).
_FUZZY_CUTOFF = 0.75


def normalize_district(raw: str, state: Optional[str] = None) -> tuple[Optional[str], bool]:
    """
    Match an extracted district name against the canonical Telangana list.

    Args:
        raw:   The extracted district value (may have typos/casing issues).
        state: The extracted state value, if available — used to skip
               correction when the customer is clearly from a different
               state (don't force a Telangana district match onto a
               genuinely different state's district name).

    Returns:
        (corrected_name, was_exact_match)
          - Exact match (case/whitespace-insensitive): (canonical_name, True)
          - Fuzzy match (typo/garbling):                (canonical_name, False)
          - No confident match, or state is clearly
            non-Telangana:                              (None, False)
    """
    if not raw:
        return None, False

    if state and "telangana" not in state.strip().casefold():
        return None, False  # different state — don't apply Telangana-specific correction

    cleaned = raw.strip()

    exact = _LOOKUP.get(cleaned.casefold())
    if exact:
        return exact, True

    matches = difflib.get_close_matches(cleaned, TELANGANA_DISTRICTS, n=1, cutoff=_FUZZY_CUTOFF)
    if matches:
        return matches[0], False

    return None, False
