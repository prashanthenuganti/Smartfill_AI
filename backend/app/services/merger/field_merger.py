"""
services/merger/field_merger.py
--------------------------------
Cross-document field merging with optimistic confidence scoring.

Algorithm per field:
  1. Collect all candidates from all documents
  2. Normalise each value (same format for comparison)
  3. Score each candidate:
       base          = OCR confidence (0-100)
       source_bonus  = government IDs trusted more than self-reported docs
       match_bonus   = +20 if same value found in 2+ documents
       format_bonus  = +10 if value passes strict format validation
  4. Pick highest-scoring candidate as the winner
  5. Set match_count = number of documents that agreed

Source trust hierarchy (higher = more trusted):
  Aadhaar    → 15 pts  (government biometric ID)
  PAN        → 15 pts  (government tax ID)
  Passport   → 12 pts  (government travel document)
  Voter ID   → 12 pts  (government electoral ID)
  DL         → 10 pts
  Certificate→  8 pts  (institutional)
  Passbook   →  8 pts  (institutional)
  Resume     →  3 pts  (self-reported)
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Optional

from backend.app.core.logging import get_logger
from backend.app.schemas.extraction import (
    AadhaarExtraction,
    DocumentExtraction,
    ExtractionResult,
    PANExtraction,
)
from backend.app.schemas.profile import CustomerProfile, FieldCandidate, ProfileField

logger = get_logger(__name__)

# ── Source trust scores ────────────────────────────────────────────────────────

# ── Base document trust (used when no field-specific rule exists) ─────────────
_SOURCE_TRUST: dict[str, float] = {
    "aadhaar":              15.0,
    "pan":                  15.0,
    "passport":             14.0,
    "voter_id":             12.0,
    "driving_license":      11.0,
    "certificate_ssc":       8.0,
    "certificate_inter":     8.0,
    "certificate_degree":    8.0,
    "bank_passbook":         8.0,
    "salary_slip":           7.0,
    "generic":               2.0,
}

# ── Field-specific trust scores (override base trust per field) ───────────────
#
# Design principles:
#   - Name:    Aadhaar is biometric-verified government ID → highest trust
#              PAN cross-checked with Aadhaar during registration → very high
#              Passport → ECNR verified → high
#   - DOB:     PAN requires DOB verification against birth records → highest
#              Passport ICAO verified → very high
#              Aadhaar self-declared during enrollment → slightly lower
#   - Address: Aadhaar undergoes address verification → highest
#              DL has residential address → high
#              Voter ID may be outdated → medium
#   - IDs:     Each ID is authoritative only for its own number
#   - Match bonus (+20) still applies when 2+ docs agree on same value,
#     so a PAN+Aadhaar match on name gets a large boost regardless of source
#
_FIELD_TRUST: dict[str, dict[str, float]] = {
    # Full name
    "name": {
        "aadhaar":          20.0,   # biometric verification
        "pan":              18.0,   # cross-checked with Aadhaar
        "passport":         17.0,   # ECNR verified
        "driving_license":  13.0,
        "voter_id":         12.0,
        "certificate_degree": 8.0,
        "certificate_inter":  7.0,
        "certificate_ssc":    7.0,
    },
    "father_name": {
        "pan":              20.0,   # father name is primary PAN field
        "aadhaar":          16.0,
        "passport":         15.0,   # back page "Name of Father/Legal Guardian"
        "driving_license":  13.0,
        "voter_id":         12.0,
    },
    "mother_name": {
        "aadhaar":          15.0,
        "passport":         15.0,   # back page "Name of Mother"
        "certificate_ssc":  12.0,
        "certificate_inter":12.0,
    },
    # Date of birth — PAN is most reliable (verified against birth records)
    "dob": {
        "pan":              22.0,   # DOB verified during PAN registration
        "passport":         20.0,   # ICAO standard verification
        "aadhaar":          16.0,   # self-declared during enrollment
        "driving_license":  14.0,
        "voter_id":         12.0,
        "certificate_ssc":  10.0,
    },
    # Gender — Aadhaar is biometric
    "gender": {
        "aadhaar":          20.0,
        "passport":         18.0,
        "driving_license":  14.0,
        "voter_id":         12.0,
    },
    # Address — Aadhaar has physical verification; passport address is
    # less frequently updated (people move without re-issuing passport)
    # so weighted slightly below Aadhaar/DL but still useful as a source
    "address":          {"aadhaar": 20.0, "driving_license": 15.0, "voter_id": 12.0, "passport": 10.0},
    "address_line1":    {"aadhaar": 20.0, "driving_license": 15.0, "voter_id": 12.0, "passport": 10.0},
    "address_line2":    {"aadhaar": 20.0, "driving_license": 15.0, "voter_id": 12.0, "passport": 10.0},
    "address_city":     {"aadhaar": 20.0, "driving_license": 15.0, "voter_id": 12.0},
    "address_district": {"aadhaar": 20.0, "driving_license": 14.0},
    "address_state":    {"aadhaar": 20.0, "driving_license": 14.0, "voter_id": 12.0, "passport": 10.0},
    "address_pincode":  {"aadhaar": 22.0, "driving_license": 15.0, "voter_id": 15.0, "passport": 10.0},
    # Nationality — only passport has this
    "nationality": {
        "passport":         30.0,   # international travel document
        "aadhaar":           5.0,
    },
    # Each ID number is only authoritative from its own document
    "aadhaar_number":   {"aadhaar": 30.0, "pan": 0.0, "passport": 0.0},
    "pan_number":       {"pan":     30.0, "aadhaar": 0.0},
    "passport_number":  {"passport":30.0},
    "voter_id":         {"voter_id":30.0},
    "dl_number":        {"driving_license": 30.0},
    # Passport dates
    "doi":  {"passport": 25.0},
    "doe":  {"passport": 25.0},
    # Education — degree fields (each isolated to its own doc type)
    "degree_name":       {"certificate_degree": 25.0},
    "degree_branch":     {"certificate_degree": 25.0},
    "degree_university": {"certificate_degree": 25.0},
    "degree_college":    {"certificate_degree": 25.0},
    "degree_roll":       {"certificate_degree": 25.0},
    "degree_year":       {"certificate_degree": 25.0},
    "degree_percentage": {"certificate_degree": 25.0},
    "degree_grade":      {"certificate_degree": 25.0},
    "degree_marks_identification": {"certificate_degree": 25.0},
    "ssc_name":        {"certificate_ssc":   25.0},
    "ssc_roll":        {"certificate_ssc":   25.0},
    "ssc_board":       {"certificate_ssc":   25.0},
    "ssc_year":        {"certificate_ssc":   25.0},
    "ssc_percentage":  {"certificate_ssc":   25.0},
    "ssc_marks_identification": {"certificate_ssc": 25.0},
    "inter_name":      {"certificate_inter": 25.0},
    "inter_roll":      {"certificate_inter": 25.0},
    "inter_board":     {"certificate_inter": 25.0},
    "inter_year":      {"certificate_inter": 25.0},
    "inter_percentage":{"certificate_inter": 25.0},
    "inter_marks_identification": {"certificate_inter": 25.0},
    # Banking
    "account_number":  {"bank_passbook": 28.0},
    "ifsc":            {"bank_passbook": 28.0},
    "bank_name":       {"bank_passbook": 25.0},
    # Mobile — Aadhaar is verified (OTP at enrollment)
    "mobile":          {"aadhaar": 18.0, "pan": 5.0},
}


def _get_trust(field_name: str, doc_type: str) -> float:
    """
    Get trust score for a specific field from a specific document.
    Field-specific rules override base document trust.
    Returns 0.0 if this document type should not contribute to this field.
    """
    field_rules = _FIELD_TRUST.get(field_name)
    if field_rules is not None:
        # Field has specific rules — use them (0.0 means "don't use this source")
        return field_rules.get(doc_type, _SOURCE_TRUST.get(doc_type, 2.0))
    # No field-specific rule — use base document trust
    return _SOURCE_TRUST.get(doc_type, 2.0)

# ── Field mapping: extraction key → profile field name ────────────────────────
# Maps what each document type calls a field to the unified profile field name

_FIELD_MAP: dict[str, str] = {
    # Name variants
    "name":             "name",
    "employee_name":    "name",
    "account_holder":   "name",
    "student_name":     "name",

    # Father name variants
    "father_name":      "father_name",
    "fathers_name":     "father_name",

    # DOB variants
    "dob":              "dob",
    "date_of_birth":    "dob",

    # Gender
    "gender":           "gender",

    # IDs
    "aadhaar_number":   "aadhaar_number",
    "pan_number":       "pan_number",
    "passport_number":  "passport_number",
    "doi":              "doi",   # Date of Issue (passport / driving licence)
    "doe":              "doe",   # Date of Expiry (passport / driving licence)
    "voter_id":         "voter_id",
    "dl_number":        "dl_number",

    # Contact
    "mobile":           "mobile",
    "phone":            "mobile",
    "address":          "address",

    # Education — SSC
    "ssc_name":         "ssc_name",
    "ssc_roll":         "ssc_roll",
    "ssc_school":       "ssc_school",
    "ssc_board":        "ssc_board",
    "ssc_year":         "ssc_year",
    "ssc_percentage":   "ssc_percentage",
    "ssc_marks_identification": "ssc_marks_identification",
    # Note: "roll_number" intentionally NOT mapped generically
    # SSC prompt returns "ssc_roll", Inter returns "inter_roll"
    # Degree prompt returns "roll_number" → maps to "roll_number" (degree enrollment no.)
    "school":           "ssc_school",
    "board":            "ssc_board",

    # Education — Intermediate
    "inter_name":       "inter_name",
    "inter_roll":       "inter_roll",
    "inter_college":    "inter_college",
    "inter_board":      "inter_board",
    "inter_year":       "inter_year",
    "inter_percentage": "inter_percentage",
    "inter_group":      "inter_group",
    "inter_marks_identification": "inter_marks_identification",

    # Education — Degree (all degree_ prefixed to avoid conflicts)
    "degree_name":       "degree_name",
    "degree_branch":     "degree_branch",
    "degree_university": "degree_university",
    "degree_college":    "degree_college",
    "degree_roll":       "degree_roll",
    "degree_year":       "degree_year",
    "degree_percentage": "degree_percentage",
    "degree_grade":      "degree_grade",
    "degree_marks_identification": "degree_marks_identification",

    # Contact
    "email":            "email",
    "address_line1":    "address_line1",
    "address_line2":    "address_line2",
    "address_city":     "address_city",
    "address_district": "address_district",
    "address_state":    "address_state",
    "address_pincode":  "address_pincode",

    # Banking
    "account_number":   "account_number",
    "account_holder":   "name",
    "ifsc":             "ifsc",
    "bank_name":        "bank_name",
    "branch":           "branch",

    # Nationality
    "nationality":      "nationality",
}

# ── Format validators ──────────────────────────────────────────────────────────

def _validate_format(field_name: str, value: str) -> bool:
    """Return True if value passes strict format check for this field."""
    checks = {
        "aadhaar_number": lambda v: bool(re.match(r'^\d{12}$', v)),
        "pan_number":     lambda v: bool(re.match(r'^[A-Z]{5}[0-9]{4}[A-Z]$', v)),
        "dob":            lambda v: bool(re.match(r'^\d{4}-\d{2}-\d{2}$', v)),
        "mobile":         lambda v: bool(re.match(r'^\d{10}$', v)),
        "ifsc":           lambda v: bool(re.match(r'^[A-Z]{4}0[A-Z0-9]{6}$', v)),
        "passport_number":lambda v: bool(re.match(r'^[A-Z]\d{7}$', v)),
    }
    check = checks.get(field_name)
    return check(value) if check else True


def _normalise_for_comparison(value: str) -> str:
    """Normalise value for cross-document comparison."""
    return re.sub(r'\s+', ' ', value.strip().lower())


class FieldMerger:
    """
    Merges extracted fields from multiple documents into
    a single unified CustomerProfile.
    """

    def merge(
        self,
        results: list[ExtractionResult],
        filenames: list[str],
    ) -> CustomerProfile:
        """
        Build a CustomerProfile from multiple ExtractionResults.

        Args:
            results:   One ExtractionResult per uploaded document.
            filenames: Original filenames (parallel to results).

        Returns:
            CustomerProfile with best value per field.
        """
        logger.info("Merging %d documents into unified profile", len(results))

        # Collect all candidates grouped by profile field name
        candidates: dict[str, list[FieldCandidate]] = defaultdict(list)

        for result, filename in zip(results, filenames):
            doc_type = result.document_type
            # trust is now computed per-field inside _collect methods via _get_trust()
            self._collect_from_typed(result, doc_type, filename, 0.0, candidates)
            self._collect_from_universal(result, doc_type, filename, 0.0, candidates)

        # Score and select the best candidate per field
        profile_data: dict[str, ProfileField] = {}
        fields_needing_review: list[str] = []
        total_confidence = 0.0
        verified = 0

        for field_name, field_candidates in candidates.items():
            profile_field = self._select_best(field_name, field_candidates)
            profile_data[field_name] = profile_field

            if profile_field.value:
                total_confidence += profile_field.confidence
                if profile_field.needs_review:
                    fields_needing_review.append(field_name)
                if profile_field.is_verified:
                    verified += 1

        populated = sum(1 for f in profile_data.values() if f.value)
        overall_conf = total_confidence / populated if populated else 0.0

        # ── Combine structured address parts into a single 'address' field ────
        # Some government forms have only ONE generic "Address" input (no
        # separate Line 1 / Line 2 / City fields). If we have the structured
        # parts (address_line1, address_line2, etc.) but the standalone
        # 'address' field is empty or only has a fragment, build a proper
        # combined single-line address from the structured parts so those
        # forms get the FULL address, not just whatever one fragment
        # happened to land in the 'address' key.
        self._combine_address_fields(profile_data)

        logger.info(
            "Merge complete | fields=%d | verified=%d | overall_conf=%.1f%%",
            populated, verified, overall_conf,
        )

        return CustomerProfile(
            **profile_data,
            documents_processed=[r.document_type for r in results],
            total_fields_extracted=populated,
            verified_fields=verified,
            fields_needing_review=fields_needing_review,
            overall_confidence=round(overall_conf, 1),
        )

    def _combine_address_fields(self, profile_data: dict[str, ProfileField]) -> None:
        """
        Build a combined single-line 'address' from structured parts
        (address_line1, address_line2, address_city, address_district,
        address_state, address_pincode) whenever those structured parts
        exist but the standalone 'address' field is missing or appears to
        be only a fragment (i.e. it doesn't already contain most of the
        structured parts' content — meaning it wasn't already a complete
        combined address from the source document).

        This handles government forms that only have a single generic
        "Address" textbox instead of separate Line 1/Line 2/City inputs —
        without this, only address_line1 (or whichever fragment first
        mapped to the bare 'address' key) would get filled there.
        """
        line1     = profile_data.get("address_line1")
        line2     = profile_data.get("address_line2")
        city      = profile_data.get("address_city")
        district  = profile_data.get("address_district")
        state     = profile_data.get("address_state")
        pincode   = profile_data.get("address_pincode")

        parts = [
            f.value for f in [line1, line2, city, district, state, pincode]
            if f and f.value
        ]
        if not parts:
            return  # no structured address data to combine

        combined_value = ", ".join(parts)

        existing = profile_data.get("address")
        needs_rebuild = (
            existing is None
            or not existing.value
            # Existing 'address' is shorter than the combined version by a
            # meaningful margin → it's likely just one fragment (e.g. only
            # address_line1), not a genuinely complete address string.
            or len(existing.value) < len(combined_value) * 0.6
        )

        if not needs_rebuild:
            return  # existing 'address' already looks complete — keep it

        # Use the highest confidence among the contributing parts, and the
        # most-trusted source document among them, as the combined field's
        # provenance metadata.
        best_part = max(
            (f for f in [line1, line2, city, district, state, pincode] if f and f.value),
            key=lambda f: f.confidence,
        )

        profile_data["address"] = ProfileField(
            value=combined_value,
            confidence=best_part.confidence,
            needs_review=best_part.needs_review,
            source_doc=best_part.source_doc,
            candidates=[],
            match_count=1,
        )
        logger.info("Combined address built from structured parts: %r", combined_value)


    # ── Candidate collection ──────────────────────────────────────────────────

    def _collect_from_typed(
        self,
        result: ExtractionResult,
        doc_type: str,
        filename: str,
        _trust: float,  # unused — trust now computed per-field via _get_trust()
        out: dict[str, list[FieldCandidate]],
    ) -> None:
        """Collect candidates from Milestone 1 typed schemas (Aadhaar/PAN)."""

        def add(field_name: str, value: Optional[str], confidence: float):
            if not value:
                return
            profile_key = _FIELD_MAP.get(field_name, field_name)
            field_trust = _get_trust(profile_key, doc_type)
            score = confidence + field_trust
            out[profile_key].append(FieldCandidate(
                value=value,
                confidence=confidence,
                source_doc=doc_type,
                source_file=filename,
                score=score,
            ))

        if result.aadhaar:
            a = result.aadhaar
            add("name",           a.name.value,           a.name.confidence)
            add("father_name",    a.father_name.value,    a.father_name.confidence)
            add("gender",         a.gender.value,         a.gender.confidence)
            add("dob",            a.dob.value,            a.dob.confidence)
            add("aadhaar_number", a.aadhaar_number.value, a.aadhaar_number.confidence)

        if result.pan:
            p = result.pan
            add("name",        p.name.value,       p.name.confidence)
            add("father_name", p.father_name.value, p.father_name.confidence)
            add("dob",         p.dob.value,        p.dob.confidence)
            add("pan_number",  p.pan_number.value, p.pan_number.confidence)

    def _collect_from_universal(
        self,
        result: ExtractionResult,
        doc_type: str,
        filename: str,
        _trust: float,  # unused — trust now computed per-field via _get_trust()
        out: dict[str, list[FieldCandidate]],
    ) -> None:
        """Collect candidates from Milestone 2 universal DocumentExtraction."""
        if not result.extraction:
            return

        for raw_field, ef in result.extraction.fields.items():
            if not ef.value:
                continue
            profile_key = _FIELD_MAP.get(raw_field, raw_field)
            field_trust = _get_trust(profile_key, doc_type)
            score = ef.confidence + field_trust
            out[profile_key].append(FieldCandidate(
                value=ef.value,
                confidence=ef.confidence,
                source_doc=doc_type,
                source_file=filename,
                score=score,
            ))

    # ── Selection algorithm ───────────────────────────────────────────────────

    def _select_best(
        self,
        field_name: str,
        candidates: list[FieldCandidate],
    ) -> ProfileField:
        """
        Score all candidates and pick the best.

        Scoring:
          base_score    = OCR confidence + source trust
          match_bonus   = +20 per additional document with same value
          format_bonus  = +10 if passes format validation
        """
        if not candidates:
            return ProfileField()

        # Group by normalised value to find matches
        groups: dict[str, list[FieldCandidate]] = defaultdict(list)
        for c in candidates:
            key = _normalise_for_comparison(c.value)
            groups[key].append(c)

        # Score each group
        best_score = -1.0
        best_group: list[FieldCandidate] = []

        for norm_val, group in groups.items():
            # Best raw score within the group
            best_in_group = max(group, key=lambda c: c.score)
            score = best_in_group.score

            # Match bonus: additional docs that agree
            match_bonus = (len(group) - 1) * 20.0
            score += match_bonus

            # Format bonus
            if _validate_format(field_name, group[0].value.strip()):
                score += 10.0

            # Update scores on candidates for display
            for c in group:
                c.score = score

            if score > best_score:
                best_score = score
                best_group = group

        winner = max(best_group, key=lambda c: c.confidence)
        match_count = len(best_group)
        final_confidence = min(winner.confidence + (match_count - 1) * 10.0, 100.0)

        return ProfileField(
            value=winner.value,
            confidence=round(final_confidence, 1),
            needs_review=final_confidence < 70.0,
            source_doc=winner.source_doc,
            candidates=candidates,
            match_count=match_count,
        )
