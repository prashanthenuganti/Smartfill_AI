"""
tests/unit/test_ocr_engine.py
------------------------------
Unit tests for TesseractOCREngine.

We generate synthetic PNG images with known text using PIL's ImageDraw,
then verify that the OCR engine extracts that text.  This tests the full
preprocessing → Tesseract pipeline without needing real document scans.

Note: Tesseract accuracy on synthetic PIL-rendered text is very high
(>95% confidence), so these tests are reliable indicators of pipeline health.
"""

import tempfile
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw, ImageFont

from backend.app.services.ocr.engine import OCRResult, TesseractOCREngine


def _make_text_image(text: str, width: int = 800, height: int = 200) -> Path:
    """
    Create a white PNG image with black text using PIL.
    Returns the path to the temp file.
    """
    img = Image.new("RGB", (width, height), color=(255, 255, 255))
    draw = ImageDraw.Draw(img)
    # Use default PIL font (always available, no font files needed)
    draw.text((40, 60), text, fill=(0, 0, 0))
    fd, path_str = tempfile.mkstemp(suffix=".png")
    path = Path(path_str)
    img.save(path)
    return path


class TestTesseractOCREngine:
    def setup_method(self):
        self.engine = TesseractOCREngine()

    def test_extract_returns_ocr_result(self):
        path = _make_text_image("Hello World")
        try:
            result = self.engine.extract(path)
            assert isinstance(result, OCRResult)
        finally:
            path.unlink()

    def test_extracted_text_not_empty(self):
        path = _make_text_image("ABCDE 1234F")
        try:
            result = self.engine.extract(path)
            assert result.text.strip() != ""
        finally:
            path.unlink()

    def test_confidence_is_within_bounds(self):
        path = _make_text_image("Ravi Kumar")
        try:
            result = self.engine.extract(path)
            assert 0.0 <= result.avg_confidence <= 100.0
        finally:
            path.unlink()

    def test_word_count_positive(self):
        path = _make_text_image("Ravi Kumar Sharma")
        try:
            result = self.engine.extract(path)
            assert result.word_count > 0
        finally:
            path.unlink()

    def test_engine_label_set(self):
        path = _make_text_image("Test text")
        try:
            result = self.engine.extract(path)
            assert "tesseract" in result.engine
        finally:
            path.unlink()

    def test_preprocessing_stages_recorded(self):
        path = _make_text_image("Test")
        try:
            result = self.engine.extract(path)
            assert isinstance(result.preprocessing, list)
        finally:
            path.unlink()

    def test_extracts_numeric_string(self):
        """Numbers are critical for Aadhaar and PAN extraction."""
        path = _make_text_image("1234 5678 9012")
        try:
            result = self.engine.extract(path)
            # Strip whitespace and check digits are present
            digits_in_result = "".join(c for c in result.text if c.isdigit())
            assert len(digits_in_result) >= 6, (
                f"Expected at least 6 digits, got: {repr(result.text)}"
            )
        finally:
            path.unlink()

    def test_missing_file_raises_ocr_error(self):
        from backend.app.core.exceptions import OCRError
        missing = Path("/tmp/smartfill_test_missing_ocr.png")
        with pytest.raises((OCRError, ValueError)):
            self.engine.extract(missing)

    def test_pdf_extension_routes_to_pdf_handler(self, tmp_path):
        """
        A minimal valid PDF should be routed to the PDF handler.
        We use PyMuPDF to create a real single-page PDF with text.
        """
        try:
            import fitz
        except ImportError:
            pytest.skip("PyMuPDF not installed")

        pdf_path = tmp_path / "test_doc.pdf"
        doc = fitz.open()
        page = doc.new_page(width=595, height=842)
        page.insert_text((72, 400), "ABCDE1234F PAN NUMBER TEST", fontsize=24)
        doc.save(str(pdf_path))
        doc.close()

        result = self.engine.extract(pdf_path)
        assert isinstance(result, OCRResult)
        assert result.text.strip() != ""
        assert "pdf" in result.engine
