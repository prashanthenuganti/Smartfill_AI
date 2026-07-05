"""
schemas/exam.py
-----------------
Request/response schemas for exam configuration (admin CRUD) and the
public read endpoints used by upload.html / review.html.

TRANSFORMS is deliberately a small, fixed list — not free-form code —
so exam field configuration stays auditable and can't introduce
arbitrary logic. Add a new named transform here (and mirror it in
review.html's applyTransform() JS function) if a real form needs one
this list doesn't cover yet.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

# Every valid value for ExamField.transform. Keep this in sync with
# APPLY_TRANSFORM in frontend/review.html.
TRANSFORMS = [
    "verbatim",
    "split_first_word",       # "Ravi Kumar Reddy" -> "Ravi"
    "split_remaining_words",  # "Ravi Kumar Reddy" -> "Kumar Reddy"
    "uppercase",
    "lowercase",
    "titlecase",
    "date_ddmmyyyy",          # -> DD-MM-YYYY
    "date_mmddyyyy",          # -> MM-DD-YYYY
    "date_yyyymmdd",          # -> YYYY-MM-DD
]

FIELD_TYPES = ["text", "date", "dropdown"]
ASSET_TYPES = ["photo", "signature"]


# ── Exam ──────────────────────────────────────────────────────────────────

class ExamCreate(BaseModel):
    code: str = Field(..., min_length=2, max_length=64)
    display_name: str = Field(..., min_length=2, max_length=255)
    category: str = Field(default="other")
    active: bool = Field(default=True)


class ExamUpdate(BaseModel):
    display_name: Optional[str] = None
    category: Optional[str] = None
    active: Optional[bool] = None


class ExamOut(BaseModel):
    id: int
    code: str
    display_name: str
    category: str
    active: bool
    field_count: int = 0

    class Config:
        from_attributes = True


# ── Exam field ────────────────────────────────────────────────────────────

class ExamFieldCreate(BaseModel):
    display_label: str = Field(..., min_length=1, max_length=255)
    field_key: str = Field(..., min_length=1, max_length=128)
    # Exactly one of these two should be provided:
    #   source_profile_key -> derived from extracted document data
    #   default_value       -> fixed constant (e.g. "ENGLISH", "NO")
    source_profile_key: Optional[str] = Field(default=None, max_length=128)
    default_value: Optional[str] = Field(default=None, max_length=500)
    transform: str = Field(default="verbatim")
    field_type: str = Field(default="text")
    required: bool = Field(default=False)
    sort_order: int = Field(default=0)


class ExamFieldUpdate(BaseModel):
    display_label: Optional[str] = None
    source_profile_key: Optional[str] = None
    default_value: Optional[str] = None
    transform: Optional[str] = None
    field_type: Optional[str] = None
    required: Optional[bool] = None
    sort_order: Optional[int] = None


class ExamFieldOut(BaseModel):
    id: int
    display_label: str
    field_key: str
    source_profile_key: Optional[str]
    default_value: Optional[str]
    transform: str
    field_type: str
    required: bool
    sort_order: int

    class Config:
        from_attributes = True


# ── Photo spec ────────────────────────────────────────────────────────────

class ExamPhotoSpecCreate(BaseModel):
    asset_type: str = Field(..., pattern="^(photo|signature)$")
    width_px: Optional[int] = Field(default=None, ge=1)
    height_px: Optional[int] = Field(default=None, ge=1)
    min_kb: Optional[int] = Field(default=None, ge=0)
    max_kb: int = Field(default=50, ge=1)
    image_format: str = Field(default="jpg")


class ExamPhotoSpecOut(BaseModel):
    id: int
    asset_type: str
    width_px: Optional[int]
    height_px: Optional[int]
    min_kb: Optional[int]
    max_kb: int
    image_format: str

    class Config:
        from_attributes = True


# ── Full exam detail (admin field-builder view) ───────────────────────────

class ExamDetailOut(BaseModel):
    id: int
    code: str
    display_name: str
    category: str
    active: bool
    fields: list[ExamFieldOut] = []
    photo_specs: list[ExamPhotoSpecOut] = []

    class Config:
        from_attributes = True


# ── CustomerProfile field keys, for the admin UI's source-key dropdown ────
# Mirrors backend/app/schemas/profile.py's CustomerProfile fields.
PROFILE_KEYS = [
    "name", "father_name", "mother_name", "gender", "dob", "nationality",
    "given_name", "surname", "place_of_birth",
    "aadhaar_number", "pan_number", "passport_number", "doi", "doe",
    "voter_id", "dl_number", "constituency", "vehicle_classes",
    "file_number", "id_number", "issuing_authority", "document_title", "date",
    "mobile", "email", "address", "address_line1", "address_line2",
    "address_city", "address_district", "address_state", "address_pincode",
    "ssc_name", "ssc_roll", "ssc_school", "ssc_board", "ssc_year",
    "ssc_percentage", "ssc_identification_mark_1", "ssc_identification_mark_2",
    "inter_name", "inter_roll", "inter_college", "inter_board", "inter_year",
    "inter_percentage", "inter_group", "inter_marks_identification",
    "degree_name", "degree_branch", "degree_university", "degree_college",
    "degree_roll", "degree_year", "degree_percentage", "degree_grade",
    "degree_marks_identification",
    "account_number", "ifsc", "bank_name", "account_holder", "account_type", "branch",
    "employee_name", "employee_id", "designation", "department", "company",
    "month_year", "basic_salary", "gross_salary", "net_salary", "pf_number",
]
