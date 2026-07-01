"""
schemas/extraction.py
---------------------
Milestone 2: Universal extraction schema.

Key change from Milestone 1:
  ALL document types use the same ExtractionResult with a flat
  `fields` dict instead of document-specific typed classes.
  This allows adding new document types without schema changes.

  Milestone 1 typed schemas (AadhaarExtraction, PANExtraction) are
  preserved for backward compatibility with existing extension UI.
"""

from typing import Optional, Any
from pydantic import BaseModel, Field


# ── Field-level wrapper ───────────────────────────────────────────────────────

class ExtractionField(BaseModel):
    """Single extracted value with confidence score."""
    value: Optional[str] = None
    confidence: float = Field(default=0.0, ge=0.0, le=100.0)
    needs_review: bool = False

    @classmethod
    def empty(cls) -> "ExtractionField":
        return cls(value=None, confidence=0.0, needs_review=True)

    @classmethod
    def from_value(
        cls,
        value: Optional[str],
        confidence: float,
        threshold: float = 60.0,
    ) -> "ExtractionField":
        return cls(
            value=value,
            confidence=round(confidence, 1),
            needs_review=(value is None or confidence < threshold),
        )


# ── Milestone 1 typed schemas (kept for extension compatibility) ──────────────

class AadhaarExtraction(BaseModel):
    name: ExtractionField = Field(default_factory=ExtractionField.empty)
    father_name: ExtractionField = Field(default_factory=ExtractionField.empty)
    gender: ExtractionField = Field(default_factory=ExtractionField.empty)
    dob: ExtractionField = Field(default_factory=ExtractionField.empty)
    year_of_birth: ExtractionField = Field(default_factory=ExtractionField.empty)
    aadhaar_number: ExtractionField = Field(default_factory=ExtractionField.empty)

    @property
    def is_complete(self) -> bool:
        return all(f.value is not None
                   for f in [self.name, self.dob, self.aadhaar_number])

    @property
    def fields_needing_review(self) -> list[str]:
        return [k for k, f in {
            "name": self.name,
            "father_name": self.father_name,
            "gender": self.gender,
            "dob": self.dob,
            "aadhaar_number": self.aadhaar_number,
        }.items() if f.needs_review]


class PANExtraction(BaseModel):
    name: ExtractionField = Field(default_factory=ExtractionField.empty)
    father_name: ExtractionField = Field(default_factory=ExtractionField.empty)
    dob: ExtractionField = Field(default_factory=ExtractionField.empty)
    pan_number: ExtractionField = Field(default_factory=ExtractionField.empty)

    @property
    def is_complete(self) -> bool:
        return all(f.value is not None for f in [self.name, self.pan_number])

    @property
    def fields_needing_review(self) -> list[str]:
        return [k for k, f in {
            "name": self.name,
            "father_name": self.father_name,
            "dob": self.dob,
            "pan_number": self.pan_number,
        }.items() if f.needs_review]


# ── Milestone 2: Universal document extraction ────────────────────────────────

class DocumentExtraction(BaseModel):
    """
    Universal extraction result for any document type.

    `fields` is a flat dict of field_name → ExtractionField.
    The AI layer populates this dict with whatever fields it finds.
    The extension renders all fields generically.

    Examples:
      Passport:   {"name": ..., "passport_number": ..., "expiry": ...}
      DL:         {"name": ..., "dl_number": ..., "valid_till": ...}
      Bank stmt:  {"account_number": ..., "ifsc": ..., "balance": ...}
    """
    document_type: str
    fields: dict[str, ExtractionField] = Field(default_factory=dict)
    raw_text: Optional[str] = None       # OCR text (for debugging)
    ocr_engine: str = "tesseract"
    avg_confidence: float = 0.0
    extraction_method: str = "parser"    # "parser" | "ai" | "hybrid"

    @property
    def is_complete(self) -> bool:
        return any(f.value is not None for f in self.fields.values())

    @property
    def fields_needing_review(self) -> list[str]:
        return [k for k, f in self.fields.items() if f.needs_review]

    def get(self, field_name: str) -> Optional[str]:
        """Convenience accessor."""
        field = self.fields.get(field_name)
        return field.value if field else None


# ── Per-document pipeline result ──────────────────────────────────────────────

class DocumentStatus:
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED  = "failed"


class ExtractionResult(BaseModel):
    """Result for one uploaded document after full pipeline."""
    document_type: str
    status: str = DocumentStatus.SUCCESS
    error: Optional[str] = None

    # Milestone 1: typed schemas (aadhaar/pan only)
    aadhaar: Optional[AadhaarExtraction] = None
    pan: Optional[PANExtraction] = None

    # Milestone 2: universal schema (all other document types)
    extraction: Optional[DocumentExtraction] = None

    ocr_engine_used: str = "tesseract"
    avg_confidence: float = Field(default=0.0, ge=0.0, le=100.0)
    preprocessing_applied: list[str] = Field(default_factory=list)
    extraction_method: str = "parser"


# ── Top-level API response ────────────────────────────────────────────────────

class ProcessingResponse(BaseModel):
    """
    The single JSON object returned by POST /api/v1/process.
    Supports both Milestone 1 (typed) and Milestone 2 (universal) results.
    """
    status: str = "success"

    # Milestone 1 typed results
    aadhaar: Optional[AadhaarExtraction] = None
    pan: Optional[PANExtraction] = None

    # Milestone 2 universal results (list — supports multiple docs)
    documents: list[DocumentExtraction] = Field(default_factory=list)

    has_errors: bool = False
    errors: list[str] = Field(default_factory=list)
    fields_needing_review: list[str] = Field(default_factory=list)
    processing_time_ms: Optional[float] = None

    @classmethod
    def from_results(
        cls,
        results: list[ExtractionResult],
        processing_time_ms: float,
    ) -> "ProcessingResponse":
        aadhaar_data = None
        pan_data = None
        documents = []
        all_errors = []
        review_fields = []

        for r in results:
            if r.status == DocumentStatus.FAILED and r.error:
                all_errors.append(f"{r.document_type}: {r.error}")

            if r.aadhaar:
                aadhaar_data = r.aadhaar
                review_fields.extend(
                    [f"aadhaar.{f}" for f in r.aadhaar.fields_needing_review]
                )
            if r.pan:
                pan_data = r.pan
                review_fields.extend(
                    [f"pan.{f}" for f in r.pan.fields_needing_review]
                )
            if r.extraction:
                documents.append(r.extraction)
                review_fields.extend(
                    [f"{r.document_type}.{f}"
                     for f in r.extraction.fields_needing_review]
                )

        return cls(
            status="success" if not all_errors else "partial",
            aadhaar=aadhaar_data,
            pan=pan_data,
            documents=documents,
            has_errors=bool(all_errors),
            errors=all_errors,
            fields_needing_review=review_fields,
            processing_time_ms=round(processing_time_ms, 1),
        )
