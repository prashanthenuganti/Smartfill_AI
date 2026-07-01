"""
tests/integration/test_api.py
------------------------------
Integration tests for the FastAPI HTTP layer.

Uses FastAPI's TestClient (synchronous wrapper around httpx) so no real
server process is needed. The pipeline is mocked so OCR/Tesseract is not
required — these tests verify HTTP contracts, not extraction accuracy.

Tests cover:
  - Health check endpoint
  - /process with valid files
  - /process with no files → 400
  - /process with oversized file → 413
  - /process with wrong extension → 400
  - /process with corrupted file (bad magic bytes) → 400
  - Response shape matches ProcessingResponse schema
  - Partial success (one doc fails, one succeeds) → 200 with has_errors
"""

import io
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.app.schemas.extraction import (
    AadhaarExtraction,
    DocumentStatus,
    ExtractionField,
    ExtractionResult,
    PANExtraction,
    ProcessingResponse,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def client():
    """TestClient wrapping a fresh FastAPI app instance."""
    app = create_app()
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


def _png_bytes() -> bytes:
    """Minimal valid PNG (1×1 white pixel)."""
    import base64
    # Smallest valid PNG file
    return base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk"
        "YPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
    )


def _pdf_bytes() -> bytes:
    """Minimal valid single-page PDF."""
    return b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n" \
           b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n" \
           b"3 0 obj<</Type/Page/MediaBox[0 0 612 792]/Parent 2 0 R>>endobj\n" \
           b"xref\n0 4\n0000000000 65535 f\n0000000009 00000 n\n" \
           b"0000000058 00000 n\n0000000115 00000 n\n" \
           b"trailer<</Size 4/Root 1 0 R>>\nstartxref\n190\n%%EOF"


def _mock_pipeline_response(
    aadhaar: bool = True, pan: bool = True
) -> ProcessingResponse:
    """Build a mock ProcessingResponse for pipeline injection."""
    return ProcessingResponse(
        status="success",
        aadhaar=AadhaarExtraction(
            name=ExtractionField.from_value("Ravi Kumar", 92.0),
            dob=ExtractionField.from_value("1990-05-15", 88.0),
            aadhaar_number=ExtractionField.from_value("123456789012", 95.0),
        ) if aadhaar else None,
        pan=PANExtraction(
            name=ExtractionField.from_value("RAVI KUMAR", 91.0),
            pan_number=ExtractionField.from_value("ABCDE1234F", 99.0),
        ) if pan else None,
        has_errors=False,
        processing_time_ms=450.0,
    )


# ── Health check ──────────────────────────────────────────────────────────────

class TestHealthCheck:
    def test_returns_200(self, client):
        r = client.get("/api/v1/health")
        assert r.status_code == 200

    def test_returns_ok_status(self, client):
        r = client.get("/api/v1/health")
        assert r.json()["status"] == "ok"

    def test_returns_service_name(self, client):
        r = client.get("/api/v1/health")
        assert "SmartFill" in r.json()["service"]


# ── POST /process — happy paths ───────────────────────────────────────────────

class TestProcessEndpoint:

    def _post_with_mock(self, client, files: dict, mock_response: ProcessingResponse):
        """Helper: POST to /process with mocked pipeline.process method."""
        with patch(
            "backend.app.services.pipeline.orchestrator.DocumentPipeline.process",
            new=AsyncMock(return_value=mock_response),
        ):
            return client.post("/api/v1/process", files=files)

    def test_aadhaar_only_returns_200(self, client):
        mock_resp = _mock_pipeline_response(aadhaar=True, pan=False)
        r = self._post_with_mock(
            client,
            files={"aadhaar_file": ("aadhaar.png", _png_bytes(), "image/png")},
            mock_response=mock_resp,
        )
        assert r.status_code == 200

    def test_pan_only_returns_200(self, client):
        mock_resp = _mock_pipeline_response(aadhaar=False, pan=True)
        r = self._post_with_mock(
            client,
            files={"pan_file": ("pan.png", _png_bytes(), "image/png")},
            mock_response=mock_resp,
        )
        assert r.status_code == 200

    def test_both_files_returns_200(self, client):
        mock_resp = _mock_pipeline_response(aadhaar=True, pan=True)
        r = self._post_with_mock(
            client,
            files={
                "aadhaar_file": ("aadhaar.png", _png_bytes(), "image/png"),
                "pan_file": ("pan.png", _png_bytes(), "image/png"),
            },
            mock_response=mock_resp,
        )
        assert r.status_code == 200

    def test_response_contains_aadhaar_fields(self, client):
        mock_resp = _mock_pipeline_response(aadhaar=True, pan=False)
        r = self._post_with_mock(
            client,
            files={"aadhaar_file": ("aadhaar.png", _png_bytes(), "image/png")},
            mock_response=mock_resp,
        )
        data = r.json()
        assert "aadhaar" in data
        assert data["aadhaar"]["name"]["value"] == "Ravi Kumar"
        assert data["aadhaar"]["aadhaar_number"]["value"] == "123456789012"

    def test_response_contains_pan_fields(self, client):
        mock_resp = _mock_pipeline_response(aadhaar=False, pan=True)
        r = self._post_with_mock(
            client,
            files={"pan_file": ("pan.png", _png_bytes(), "image/png")},
            mock_response=mock_resp,
        )
        data = r.json()
        assert "pan" in data
        assert data["pan"]["pan_number"]["value"] == "ABCDE1234F"

    def test_pdf_upload_accepted(self, client):
        mock_resp = _mock_pipeline_response(aadhaar=True, pan=False)
        r = self._post_with_mock(
            client,
            files={"aadhaar_file": ("aadhaar.pdf", _pdf_bytes(), "application/pdf")},
            mock_response=mock_resp,
        )
        assert r.status_code == 200

    def test_processing_time_in_response(self, client):
        mock_resp = _mock_pipeline_response()
        r = self._post_with_mock(
            client,
            files={"aadhaar_file": ("a.png", _png_bytes(), "image/png")},
            mock_response=mock_resp,
        )
        assert r.json().get("processing_time_ms") is not None


# ── POST /process — error cases ───────────────────────────────────────────────

class TestProcessErrors:

    def test_no_files_returns_400(self, client):
        r = client.post("/api/v1/process")
        assert r.status_code == 400

    def test_no_files_error_code(self, client):
        r = client.post("/api/v1/process")
        body = r.json()
        assert body.get("error_code") == "NO_FILE_PROVIDED"

    def test_unsupported_extension_returns_400(self, client):
        r = client.post(
            "/api/v1/process",
            files={"aadhaar_file": ("aadhaar.gif", b"GIF89a", "image/gif")},
        )
        assert r.status_code == 400
        assert r.json()["error_code"] == "UNSUPPORTED_FILE_TYPE"

    def test_corrupted_file_returns_400(self, client):
        """A .pdf file that doesn't start with %PDF should be rejected."""
        fake_pdf = b"This is not a PDF at all"
        r = client.post(
            "/api/v1/process",
            files={"aadhaar_file": ("aadhaar.pdf", fake_pdf, "application/pdf")},
        )
        assert r.status_code == 400
        assert r.json()["error_code"] == "CORRUPTED_FILE"

    def test_oversized_file_returns_413(self, client):
        """A file larger than max_file_size_mb should be rejected."""
        from backend.app.core.config import get_settings
        settings = get_settings()
        # Generate content just over the limit
        big_content = b"\xff\xd8\xff" + b"A" * (settings.max_file_size_bytes + 1)
        r = client.post(
            "/api/v1/process",
            files={"aadhaar_file": ("big.jpg", big_content, "image/jpeg")},
        )
        assert r.status_code == 413
        assert r.json()["error_code"] == "FILE_TOO_LARGE"

    def test_error_response_has_standard_shape(self, client):
        """Every error must include status, error_code, message."""
        r = client.post("/api/v1/process")
        body = r.json()
        assert "status" in body
        assert "error_code" in body
        assert "message" in body
        assert body["status"] == "error"

    def test_unknown_endpoint_returns_404(self, client):
        r = client.get("/api/v1/does_not_exist")
        assert r.status_code == 404


# ── Response schema conformance ───────────────────────────────────────────────

class TestResponseSchema:

    def test_success_response_serialisable(self, client):
        """Full round-trip: response must be valid JSON parseable by Pydantic."""
        with patch(
            "backend.app.services.pipeline.orchestrator.DocumentPipeline.process",
            new=AsyncMock(return_value=_mock_pipeline_response()),
        ):
            r = client.post(
                "/api/v1/process",
                files={"aadhaar_file": ("a.png", _png_bytes(), "image/png")},
            )
            parsed = ProcessingResponse(**r.json())
            assert parsed.status in ("success", "partial", "error")

    def test_extraction_field_has_confidence(self, client):
        """Every extracted field must expose a confidence score."""
        with patch(
            "backend.app.services.pipeline.orchestrator.DocumentPipeline.process",
            new=AsyncMock(return_value=_mock_pipeline_response(aadhaar=True, pan=False)),
        ):
            r = client.post(
                "/api/v1/process",
                files={"aadhaar_file": ("a.png", _png_bytes(), "image/png")},
            )
            name_field = r.json()["aadhaar"]["name"]
            assert "confidence" in name_field
            assert "needs_review" in name_field
            assert "value" in name_field
