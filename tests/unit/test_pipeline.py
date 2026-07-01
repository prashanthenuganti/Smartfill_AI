"""
tests/unit/test_pipeline.py
----------------------------
Unit tests for DocumentPipeline orchestrator.

OCR engine and parsers are mocked so tests are fast and deterministic.
Tests verify orchestration logic:
  - Correct routing of document types to parsers
  - Concurrent processing (both documents processed)
  - Temp file cleanup on success and failure
  - Error handling: OCR failure, parser failure, no documents
  - Response assembly from multiple results
"""

import asyncio
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from backend.app.schemas.documents import DocumentType, UploadedFile
from backend.app.schemas.extraction import (
    AadhaarExtraction,
    DocumentStatus,
    ExtractionField,
    ExtractionResult,
    PANExtraction,
)
from backend.app.services.ocr.engine import OCRResult
from backend.app.services.pipeline.orchestrator import DocumentPipeline


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_tmp_file() -> Path:
    """Create a real temp file on disk (pipeline will try to delete it)."""
    fd, path = tempfile.mkstemp(suffix=".png")
    import os; os.close(fd)
    return Path(path)


def _make_uploaded_file(doc_type: DocumentType, tmp_path: Path) -> UploadedFile:
    return UploadedFile(
        document_type=doc_type,
        original_filename=f"test_{doc_type.value}.png",
        content_type="image/png",
        size_bytes=1024,
        tmp_path=tmp_path,
    )


def _make_ocr_result(confidence: float = 85.0) -> OCRResult:
    return OCRResult(
        text="Government of India\nName: RAVI KUMAR\n1234 5678 9012",
        avg_confidence=confidence,
        word_count=10,
        engine="tesseract_pass1",
        preprocessing=["greyscale"],
    )


def _make_aadhaar_result() -> ExtractionResult:
    return ExtractionResult(
        document_type="aadhaar",
        status=DocumentStatus.SUCCESS,
        aadhaar=AadhaarExtraction(
            name=ExtractionField.from_value("Ravi Kumar", 90.0),
            dob=ExtractionField.from_value("1990-05-15", 88.0),
            aadhaar_number=ExtractionField.from_value("123456789012", 95.0),
        ),
        avg_confidence=91.0,
    )


def _make_pan_result() -> ExtractionResult:
    return ExtractionResult(
        document_type="pan",
        status=DocumentStatus.SUCCESS,
        pan=PANExtraction(
            name=ExtractionField.from_value("RAVI KUMAR", 92.0),
            pan_number=ExtractionField.from_value("ABCDE1234F", 98.0),
        ),
        avg_confidence=95.0,
    )


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestDocumentPipeline:

    def setup_method(self):
        self.pipeline = DocumentPipeline()

    # ── Basic functionality ───────────────────────────────────────────────────

    def test_pipeline_instantiates(self):
        assert self.pipeline is not None

    def test_empty_file_list_returns_error_response(self):
        response = asyncio.run(self.pipeline.process([]))
        assert response.status == "error"
        assert response.has_errors is True
        assert len(response.errors) > 0

    # ── Successful processing ─────────────────────────────────────────────────

    @patch.object(DocumentPipeline, '_run_ocr')
    @patch.object(DocumentPipeline, '_run_parser')
    def test_aadhaar_processed_successfully(self, mock_parser, mock_ocr):
        mock_ocr.return_value = _make_ocr_result()
        mock_parser.return_value = _make_aadhaar_result()

        tmp = _make_tmp_file()
        file = _make_uploaded_file(DocumentType.AADHAAR, tmp)

        response = asyncio.run(self.pipeline.process([file]))

        assert response.aadhaar is not None
        assert response.aadhaar.name.value == "Ravi Kumar"
        assert response.has_errors is False

    @patch.object(DocumentPipeline, '_run_ocr')
    @patch.object(DocumentPipeline, '_run_parser')
    def test_pan_processed_successfully(self, mock_parser, mock_ocr):
        mock_ocr.return_value = _make_ocr_result()
        mock_parser.return_value = _make_pan_result()

        tmp = _make_tmp_file()
        file = _make_uploaded_file(DocumentType.PAN, tmp)

        response = asyncio.run(self.pipeline.process([file]))

        assert response.pan is not None
        assert response.pan.pan_number.value == "ABCDE1234F"

    @patch.object(DocumentPipeline, '_run_ocr')
    @patch.object(DocumentPipeline, '_run_parser')
    def test_both_documents_processed(self, mock_parser, mock_ocr):
        """Both Aadhaar and PAN in one call → both appear in response."""
        mock_ocr.return_value = _make_ocr_result()

        def _parser_side_effect(ocr_result, doc_type):
            if doc_type == DocumentType.AADHAAR:
                return _make_aadhaar_result()
            return _make_pan_result()

        mock_parser.side_effect = _parser_side_effect

        aadhaar_tmp = _make_tmp_file()
        pan_tmp = _make_tmp_file()

        response = asyncio.run(self.pipeline.process([
            _make_uploaded_file(DocumentType.AADHAAR, aadhaar_tmp),
            _make_uploaded_file(DocumentType.PAN, pan_tmp),
        ]))

        assert response.aadhaar is not None
        assert response.pan is not None
        assert response.status == "success"

    # ── Temp file cleanup ─────────────────────────────────────────────────────

    @patch.object(DocumentPipeline, '_run_ocr')
    @patch.object(DocumentPipeline, '_run_parser')
    def test_temp_file_deleted_after_success(self, mock_parser, mock_ocr):
        mock_ocr.return_value = _make_ocr_result()
        mock_parser.return_value = _make_aadhaar_result()

        tmp = _make_tmp_file()
        assert tmp.exists()

        asyncio.run(self.pipeline.process([
            _make_uploaded_file(DocumentType.AADHAAR, tmp)
        ]))

        assert not tmp.exists(), "Temp file should be deleted after processing"

    @patch.object(DocumentPipeline, '_run_ocr')
    def test_temp_file_deleted_even_on_ocr_failure(self, mock_ocr):
        from backend.app.core.exceptions import OCRError as AppOCRError
        mock_ocr.side_effect = AppOCRError("Tesseract failed")

        tmp = _make_tmp_file()
        assert tmp.exists()

        asyncio.run(self.pipeline.process([
            _make_uploaded_file(DocumentType.AADHAAR, tmp)
        ]))

        assert not tmp.exists(), "Temp file must be deleted even when OCR fails"

    # ── Error handling ────────────────────────────────────────────────────────

    @patch.object(DocumentPipeline, '_run_ocr')
    def test_ocr_failure_returns_failed_result(self, mock_ocr):
        from backend.app.core.exceptions import OCRError as AppOCRError
        mock_ocr.side_effect = AppOCRError("Empty text returned")

        tmp = _make_tmp_file()
        response = asyncio.run(self.pipeline.process([
            _make_uploaded_file(DocumentType.AADHAAR, tmp)
        ]))

        assert response.has_errors is True
        assert any("OCR" in e for e in response.errors)

    @patch.object(DocumentPipeline, '_run_ocr')
    def test_unexpected_exception_is_caught(self, mock_ocr):
        mock_ocr.side_effect = RuntimeError("Unexpected crash")

        tmp = _make_tmp_file()
        # Must not raise — pipeline catches all exceptions
        response = asyncio.run(self.pipeline.process([
            _make_uploaded_file(DocumentType.AADHAAR, tmp)
        ]))

        assert response is not None
        assert response.has_errors is True

    # ── Routing ───────────────────────────────────────────────────────────────

    def test_aadhaar_parser_registered(self):
        from backend.app.schemas.documents import DocumentType
        assert DocumentType.AADHAAR in self.pipeline._parsers

    def test_pan_parser_registered(self):
        assert DocumentType.PAN in self.pipeline._parsers

    def test_aadhaar_parser_is_correct_type(self):
        from backend.app.services.parsers.aadhaar_parser import AadhaarParser
        assert isinstance(self.pipeline._parsers[DocumentType.AADHAAR], AadhaarParser)

    def test_pan_parser_is_correct_type(self):
        from backend.app.services.parsers.pan_parser import PANParser
        assert isinstance(self.pipeline._parsers[DocumentType.PAN], PANParser)

    # ── Timing ───────────────────────────────────────────────────────────────

    @patch.object(DocumentPipeline, '_run_ocr')
    @patch.object(DocumentPipeline, '_run_parser')
    def test_processing_time_recorded(self, mock_parser, mock_ocr):
        mock_ocr.return_value = _make_ocr_result()
        mock_parser.return_value = _make_aadhaar_result()

        tmp = _make_tmp_file()
        response = asyncio.run(self.pipeline.process([
            _make_uploaded_file(DocumentType.AADHAAR, tmp)
        ]))

        assert response.processing_time_ms is not None
        assert response.processing_time_ms >= 0

    # ── Response shape ────────────────────────────────────────────────────────

    @patch.object(DocumentPipeline, '_run_ocr')
    @patch.object(DocumentPipeline, '_run_parser')
    def test_response_is_processing_response_type(self, mock_parser, mock_ocr):
        from backend.app.schemas.extraction import ProcessingResponse
        mock_ocr.return_value = _make_ocr_result()
        mock_parser.return_value = _make_aadhaar_result()

        tmp = _make_tmp_file()
        response = asyncio.run(self.pipeline.process([
            _make_uploaded_file(DocumentType.AADHAAR, tmp)
        ]))

        assert isinstance(response, ProcessingResponse)
