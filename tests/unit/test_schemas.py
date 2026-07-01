"""
tests/unit/test_schemas.py
--------------------------
Unit tests for all Pydantic schema models.

These tests have zero external dependencies — no OCR, no FastAPI, no files.
They verify that schemas behave correctly as data contracts.
"""

import pytest
from backend.app.schemas import (
    AadhaarExtraction,
    DocumentStatus,
    DocumentType,
    ErrorCode,
    ErrorResponse,
    ExtractionField,
    ExtractionResult,
    PANExtraction,
    ProcessingResponse,
)


# ── ExtractionField ───────────────────────────────────────────────────────────

class TestExtractionField:
    def test_empty_factory(self):
        field = ExtractionField.empty()
        assert field.value is None
        assert field.confidence == 0.0
        assert field.needs_review is True

    def test_from_value_high_confidence(self):
        field = ExtractionField.from_value("Ravi Kumar", 95.0, threshold=60.0)
        assert field.value == "Ravi Kumar"
        assert field.confidence == 95.0
        assert field.needs_review is False

    def test_from_value_low_confidence_flags_review(self):
        field = ExtractionField.from_value("Ravi Kumar", 45.0, threshold=60.0)
        assert field.needs_review is True

    def test_from_value_none_flags_review(self):
        field = ExtractionField.from_value(None, 100.0, threshold=60.0)
        assert field.needs_review is True

    def test_confidence_rounded(self):
        field = ExtractionField.from_value("Test", 87.6789, threshold=60.0)
        assert field.confidence == 87.7

    def test_confidence_bounds(self):
        with pytest.raises(Exception):
            ExtractionField(value="x", confidence=101.0)
        with pytest.raises(Exception):
            ExtractionField(value="x", confidence=-1.0)


# ── AadhaarExtraction ─────────────────────────────────────────────────────────

class TestAadhaarExtraction:
    def _make_field(self, value: str) -> ExtractionField:
        return ExtractionField.from_value(value, 95.0)

    def test_is_complete_when_mandatory_fields_present(self):
        aadhaar = AadhaarExtraction(
            name=self._make_field("Ravi Kumar"),
            dob=self._make_field("1990-05-15"),
            aadhaar_number=self._make_field("123456789012"),
        )
        assert aadhaar.is_complete is True

    def test_is_incomplete_when_name_missing(self):
        aadhaar = AadhaarExtraction(
            dob=self._make_field("1990-05-15"),
            aadhaar_number=self._make_field("123456789012"),
        )
        assert aadhaar.is_complete is False

    def test_fields_needing_review(self):
        aadhaar = AadhaarExtraction(
            name=ExtractionField.from_value("Ravi", 90.0),
            dob=ExtractionField.from_value("1990-05-15", 40.0),  # low confidence
            aadhaar_number=ExtractionField.empty(),              # missing
        )
        review = aadhaar.fields_needing_review
        assert "dob" in review
        assert "aadhaar_number" in review
        assert "name" not in review

    def test_default_fields_are_empty(self):
        aadhaar = AadhaarExtraction()
        assert aadhaar.name.value is None
        assert aadhaar.is_complete is False


# ── PANExtraction ─────────────────────────────────────────────────────────────

class TestPANExtraction:
    def test_is_complete(self):
        pan = PANExtraction(
            name=ExtractionField.from_value("Ravi Kumar", 95.0),
            pan_number=ExtractionField.from_value("ABCDE1234F", 99.0),
        )
        assert pan.is_complete is True

    def test_fields_needing_review_with_missing_pan(self):
        pan = PANExtraction(
            name=ExtractionField.from_value("Ravi Kumar", 95.0),
        )
        assert "pan_number" in pan.fields_needing_review


# ── ProcessingResponse ────────────────────────────────────────────────────────

class TestProcessingResponse:
    def _make_aadhaar_result(self) -> ExtractionResult:
        return ExtractionResult(
            document_type=DocumentType.AADHAAR,
            status=DocumentStatus.SUCCESS,
            aadhaar=AadhaarExtraction(
                name=ExtractionField.from_value("Ravi Kumar", 95.0),
                dob=ExtractionField.from_value("1990-05-15", 90.0),
                aadhaar_number=ExtractionField.from_value("123456789012", 99.0),
            ),
            avg_confidence=94.7,
        )

    def _make_pan_result(self) -> ExtractionResult:
        return ExtractionResult(
            document_type=DocumentType.PAN,
            status=DocumentStatus.SUCCESS,
            pan=PANExtraction(
                name=ExtractionField.from_value("RAVI KUMAR", 95.0),
                pan_number=ExtractionField.from_value("ABCDE1234F", 99.0),
            ),
            avg_confidence=97.0,
        )

    def test_from_results_success(self):
        results = [self._make_aadhaar_result(), self._make_pan_result()]
        response = ProcessingResponse.from_results(results, processing_time_ms=1200.0)
        assert response.status == "success"
        assert response.aadhaar is not None
        assert response.pan is not None
        assert response.has_errors is False
        assert response.processing_time_ms == 1200.0

    def test_from_results_with_failed_document(self):
        failed = ExtractionResult(
            document_type=DocumentType.AADHAAR,
            status=DocumentStatus.FAILED,
            error="OCR returned empty text",
        )
        response = ProcessingResponse.from_results([failed], processing_time_ms=500.0)
        assert response.has_errors is True
        assert len(response.errors) == 1
        assert "aadhaar" in response.errors[0]

    def test_review_fields_propagated(self):
        result = ExtractionResult(
            document_type=DocumentType.AADHAAR,
            status=DocumentStatus.PARTIAL,
            aadhaar=AadhaarExtraction(
                name=ExtractionField.from_value("Ravi", 90.0),
                dob=ExtractionField.from_value(None, 0.0),  # missing
            ),
        )
        response = ProcessingResponse.from_results([result], processing_time_ms=800.0)
        assert "aadhaar.dob" in response.fields_needing_review


# ── ErrorResponse ─────────────────────────────────────────────────────────────

class TestErrorResponse:
    def test_structure(self):
        err = ErrorResponse(
            error_code=ErrorCode.FILE_TOO_LARGE,
            message="File exceeds 10 MB limit.",
        )
        assert err.status == "error"
        assert err.error_code == "FILE_TOO_LARGE"
        assert err.details == {}

    def test_with_details(self):
        err = ErrorResponse(
            error_code=ErrorCode.VALIDATION_FAILED,
            message="PAN format invalid.",
            details={"received": "ABCDE123", "expected_pattern": "AAAAA0000A"},
        )
        assert "received" in err.details
