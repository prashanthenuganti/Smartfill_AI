"""
schemas/documents.py
--------------------
Document type definitions for Milestone 2.

Supports all document types across all 7 phases of the roadmap.
The pipeline treats every document identically — only the AI
understanding layer knows what fields to extract per type.
"""

from enum import Enum
from pathlib import Path
from pydantic import BaseModel, Field


class DocumentType(str, Enum):
    """
    All supported document types.
    Adding a new type here is all that's needed to support it —
    no new parsers, no new routes.
    """
    # Phase 1 — Identity (Milestone 1)
    AADHAAR     = "aadhaar"
    PAN         = "pan"

    # Phase 2 — Government ID (Milestone 2)
    PASSPORT    = "passport"
    DRIVING_LICENSE = "driving_license"
    VOTER_ID    = "voter_id"

    # Phase 3 — Education
    CERTIFICATE_SSC    = "certificate_ssc"
    CERTIFICATE_INTER  = "certificate_inter"
    CERTIFICATE_DEGREE = "certificate_degree"
    MARKS_MEMO         = "marks_memo"

    # Phase 4 — Financial
    BANK_PASSBOOK   = "bank_passbook"
    BANK_STATEMENT  = "bank_statement"
    SALARY_SLIP     = "salary_slip"
    CANCELLED_CHEQUE = "cancelled_cheque"
    GST_CERTIFICATE = "gst_certificate"
    INCOME_CERTIFICATE = "income_certificate"

    # Phase 5 — Government
    RATION_CARD      = "ration_card"
    CASTE_CERTIFICATE = "caste_certificate"
    RESIDENCE_CERTIFICATE = "residence_certificate"
    BIRTH_CERTIFICATE = "birth_certificate"

    # Phase 6 — Employment
    RESUME           = "resume"
    EXPERIENCE_LETTER = "experience_letter"
    OFFER_LETTER     = "offer_letter"

    # Phase 7 — Property
    ELECTRICITY_BILL = "electricity_bill"
    WATER_BILL       = "water_bill"

    # Auto-detect (let the classifier decide)
    AUTO = "auto"


# Human-readable labels for the Chrome Extension dropdown
DOCUMENT_LABELS: dict[DocumentType, str] = {
    DocumentType.AADHAAR:          "Aadhaar Card",
    DocumentType.PAN:              "PAN Card",
    DocumentType.PASSPORT:         "Passport",
    DocumentType.DRIVING_LICENSE:  "Driving Licence",
    DocumentType.VOTER_ID:         "Voter ID",
    DocumentType.CERTIFICATE_SSC:  "SSC Certificate",
    DocumentType.CERTIFICATE_INTER:"Intermediate Certificate",
    DocumentType.CERTIFICATE_DEGREE:"Degree Certificate",
    DocumentType.MARKS_MEMO:       "Marks Memo",
    DocumentType.BANK_PASSBOOK:    "Bank Passbook",
    DocumentType.BANK_STATEMENT:   "Bank Statement",
    DocumentType.SALARY_SLIP:      "Salary Slip",
    DocumentType.CANCELLED_CHEQUE: "Cancelled Cheque",
    DocumentType.GST_CERTIFICATE:  "GST Certificate",
    DocumentType.INCOME_CERTIFICATE:"Income Certificate",
    DocumentType.RATION_CARD:      "Ration Card",
    DocumentType.CASTE_CERTIFICATE:"Caste Certificate",
    DocumentType.RESIDENCE_CERTIFICATE: "Residence Certificate",
    DocumentType.BIRTH_CERTIFICATE:"Birth Certificate",
    DocumentType.RESUME:           "Resume / CV",
    DocumentType.EXPERIENCE_LETTER:"Experience Letter",
    DocumentType.OFFER_LETTER:     "Offer Letter",
    DocumentType.ELECTRICITY_BILL: "Electricity Bill",
    DocumentType.WATER_BILL:       "Water Bill",
    DocumentType.AUTO:             "Auto Detect",
}

# Which document types are available in Milestone 2
MILESTONE_2_TYPES = {
    DocumentType.AADHAAR,
    DocumentType.PAN,
    DocumentType.PASSPORT,
    DocumentType.DRIVING_LICENSE,
    DocumentType.VOTER_ID,
    DocumentType.AUTO,
}


class UploadedFile(BaseModel):
    """
    Internal representation of a validated uploaded file.
    document_type can be AUTO — the classifier resolves it.
    """
    document_type: DocumentType
    original_filename: str = Field(..., min_length=1, max_length=255)
    content_type: str
    size_bytes: int = Field(..., ge=1)
    tmp_path: Path

    model_config = {"arbitrary_types_allowed": True}
