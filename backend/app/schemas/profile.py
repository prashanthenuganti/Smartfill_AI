"""
schemas/profile.py
------------------
Unified Customer Profile — the output of cross-document field merging.

When an operator uploads multiple documents for the same customer,
the FieldMerger compares values across documents and builds one
authoritative profile with the best value per field.

Each field in the profile knows:
  - The winning value
  - Which document it came from
  - The merged confidence score
  - All candidate values (for operator to inspect)
"""

from typing import Optional
from pydantic import BaseModel, Field


class FieldCandidate(BaseModel):
    """One extracted value from one document."""
    value: str
    confidence: float
    source_doc: str          # e.g. "aadhaar", "pan", "passport"
    source_file: str         # original filename
    score: float = 0.0       # final merged score (higher = preferred)


class ProfileField(BaseModel):
    """
    A single field in the unified customer profile.

    `value` is the best candidate selected by the FieldMerger.
    `candidates` holds all extracted values so the operator can
    see where the data came from and override if needed.
    """
    value: Optional[str] = None
    confidence: float = 0.0
    needs_review: bool = False
    source_doc: str = ""            # which document provided the winning value
    candidates: list[FieldCandidate] = Field(default_factory=list)
    match_count: int = 1            # how many docs agreed on this value

    @property
    def is_verified(self) -> bool:
        """True when 2+ documents agree on the same value."""
        return self.match_count >= 2


class CustomerProfile(BaseModel):
    """
    Unified customer profile built from all uploaded documents.

    Fields are grouped by category to make the UI easier to render.
    All categories use the same ProfileField structure.
    """

    # ── Personal ──────────────────────────────────────────────────────────────
    name: ProfileField = Field(default_factory=ProfileField)
    father_name: ProfileField = Field(default_factory=ProfileField)
    mother_name: ProfileField = Field(default_factory=ProfileField)
    gender: ProfileField = Field(default_factory=ProfileField)
    dob: ProfileField = Field(default_factory=ProfileField)
    nationality: ProfileField = Field(default_factory=ProfileField)

    # ── Government IDs ────────────────────────────────────────────────────────
    aadhaar_number: ProfileField = Field(default_factory=ProfileField)
    pan_number: ProfileField = Field(default_factory=ProfileField)
    passport_number: ProfileField = Field(default_factory=ProfileField)
    doi: ProfileField = Field(default_factory=ProfileField)  # Date of Issue (passport / DL)
    doe: ProfileField = Field(default_factory=ProfileField)  # Date of Expiry (passport / DL)
    voter_id: ProfileField = Field(default_factory=ProfileField)
    dl_number: ProfileField = Field(default_factory=ProfileField)

    # ── Contact ───────────────────────────────────────────────────────────────
    mobile: ProfileField = Field(default_factory=ProfileField)
    email: ProfileField = Field(default_factory=ProfileField)
    address: ProfileField = Field(default_factory=ProfileField)
    address_line1: ProfileField = Field(default_factory=ProfileField)
    address_line2: ProfileField = Field(default_factory=ProfileField)
    address_city: ProfileField = Field(default_factory=ProfileField)
    address_district: ProfileField = Field(default_factory=ProfileField)
    address_state: ProfileField = Field(default_factory=ProfileField)
    address_pincode: ProfileField = Field(default_factory=ProfileField)

    # ── Education — SSC ───────────────────────────────────────────────────────
    ssc_name: ProfileField = Field(default_factory=ProfileField)
    ssc_roll: ProfileField = Field(default_factory=ProfileField)
    ssc_school: ProfileField = Field(default_factory=ProfileField)
    ssc_board: ProfileField = Field(default_factory=ProfileField)
    ssc_year: ProfileField = Field(default_factory=ProfileField)
    ssc_percentage: ProfileField = Field(default_factory=ProfileField)
    ssc_identification_mark_1: ProfileField = Field(default_factory=ProfileField)
    ssc_identification_mark_2: ProfileField = Field(default_factory=ProfileField)

    # ── Education — Intermediate ──────────────────────────────────────────────
    inter_name: ProfileField = Field(default_factory=ProfileField)
    inter_roll: ProfileField = Field(default_factory=ProfileField)
    inter_college: ProfileField = Field(default_factory=ProfileField)
    inter_board: ProfileField = Field(default_factory=ProfileField)
    inter_year: ProfileField = Field(default_factory=ProfileField)
    inter_percentage: ProfileField = Field(default_factory=ProfileField)
    inter_group: ProfileField = Field(default_factory=ProfileField)
    inter_marks_identification: ProfileField = Field(default_factory=ProfileField)

    # ── Education — Degree ────────────────────────────────────────────────────
    degree_name:       ProfileField = Field(default_factory=ProfileField)
    degree_branch:     ProfileField = Field(default_factory=ProfileField)
    degree_university: ProfileField = Field(default_factory=ProfileField)
    degree_college:    ProfileField = Field(default_factory=ProfileField)
    degree_roll:       ProfileField = Field(default_factory=ProfileField)
    degree_year:       ProfileField = Field(default_factory=ProfileField)
    degree_percentage: ProfileField = Field(default_factory=ProfileField)
    degree_grade:      ProfileField = Field(default_factory=ProfileField)
    degree_marks_identification: ProfileField = Field(default_factory=ProfileField)

    # ── Banking ───────────────────────────────────────────────────────────────
    account_number: ProfileField = Field(default_factory=ProfileField)
    ifsc: ProfileField = Field(default_factory=ProfileField)
    bank_name: ProfileField = Field(default_factory=ProfileField)

    # ── Meta ──────────────────────────────────────────────────────────────────
    documents_processed: list[str] = Field(default_factory=list)
    total_fields_extracted: int = 0
    verified_fields: int = 0          # fields confirmed by 2+ documents
    fields_needing_review: list[str] = Field(default_factory=list)
    overall_confidence: float = 0.0
    processing_time_ms: float = 0.0

    def to_flat_dict(self) -> dict[str, Optional[str]]:
        """Return flat field_name → value dict for form filling."""
        result = {}
        for field_name, field_val in self.__fields__.items():
            attr = getattr(self, field_name)
            if isinstance(attr, ProfileField):
                result[field_name] = attr.value
        return result

    def populated_fields(self) -> dict[str, "ProfileField"]:
        """Return only fields that have a value."""
        result = {}
        for field_name in self.__fields__:
            attr = getattr(self, field_name)
            if isinstance(attr, ProfileField) and attr.value:
                result[field_name] = attr
        return result
