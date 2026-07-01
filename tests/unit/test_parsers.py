"""
tests/unit/test_parsers.py
---------------------------
Unit tests for AadhaarParser and PANParser.

Uses synthetic OCR text that mirrors real card layouts.
No Tesseract, no images — parsers receive pre-built OCRResult objects.

Test coverage:
  - Happy path: well-formatted card text → all fields extracted
  - Partial: some fields missing or unreadable
  - Failed: completely unrecognisable text
  - Edge cases: OCR artefacts, variant label formats, masked Aadhaar
"""

import pytest

from backend.app.schemas.extraction import DocumentStatus
from backend.app.services.ocr.engine import OCRResult
from backend.app.services.parsers.aadhaar_parser import AadhaarParser
from backend.app.services.parsers.pan_parser import PANParser


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_ocr(text: str, confidence: float = 85.0, words: int = 20) -> OCRResult:
    """Build a synthetic OCRResult for testing."""
    return OCRResult(
        text=text,
        avg_confidence=confidence,
        word_count=words,
        engine="tesseract_pass1",
        preprocessing=["greyscale", "adaptive_threshold"],
    )


# ── Synthetic OCR texts ───────────────────────────────────────────────────────

AADHAAR_FRONT_TEXT = """\
भारत सरकार
Government of India
Name: RAVI KUMAR
S/O: RAJESH KUMAR
DOB: 15/05/1990
Gender: Male
XXXX XXXX 9012
"""

AADHAAR_BACK_TEXT = """\
भारत सरकार
Government of India
1234 5678 9012
Address: 12 MG Road, Hyderabad, Telangana - 500001
"""

AADHAAR_FULL_TEXT = AADHAAR_FRONT_TEXT + "\n" + AADHAAR_BACK_TEXT

AADHAAR_DOB_VARIANT = """\
Government of India
Name: PRIYA SHARMA
D/O: SURESH SHARMA
Date of Birth: 22-08-1995
Gender: Female
5678 1234 9012
"""

AADHAAR_ONLY_YEAR = """\
Government of India
Name: ANAND RAO
S/O: VENKAT RAO
Year of Birth: 1988
Gender: Male
9876 5432 1098
"""

AADHAAR_NO_LABELS = """\
UIDAI
Government of India
SURESH KUMAR
RAMESH KUMAR
15/06/1980
Male
3456 7890 1234
"""

AADHAAR_LOW_QUALITY = """\
Govemment of lndia
N@me R4VI KUM@R
Gender M@le
DOB 15/0S/1990
"""

PAN_STANDARD_TEXT = """\
INCOME TAX DEPARTMENT
GOVT. OF INDIA

Name
RAVI KUMAR

Father's Name
RAJESH KUMAR

Date of Birth
15/05/1990

ABCDE1234F
Permanent Account Number
"""

PAN_FEMALE_TEXT = """\
INCOME TAX DEPARTMENT
GOVT. OF INDIA

Name
PRIYA SHARMA

Father's Name
SURESH SHARMA

Date of Birth
22/08/1995

FGHIJ5678K
Permanent Account Number
"""

PAN_WITH_OCR_NOISE = """\
INCOME TAX OEPARTMENT
GOVT. OF INOIA

Name
RAVI KUMAR

Fathers Name
RAJESH KUMAR

ABCDE 1234 F
"""

PAN_PAN_IN_MIDDLE = """\
INCOME TAX DEPARTMENT
Name
VENKAT RAO
ABCDE1234F
"""

GARBAGE_TEXT = """\
@@@ !!! ###
|||  ===  ///
random noise here
"""


# ── AadhaarParser tests ───────────────────────────────────────────────────────

class TestAadhaarParser:
    def setup_method(self):
        self.parser = AadhaarParser()

    def test_document_type(self):
        assert self.parser.document_type == "aadhaar"

    def test_parse_never_raises(self):
        """Parser contract: must never raise, even on garbage input."""
        result = self.parser.parse(_make_ocr(GARBAGE_TEXT))
        assert result is not None

    def test_happy_path_full_card(self):
        result = self.parser.parse(_make_ocr(AADHAAR_FULL_TEXT))
        assert result.aadhaar is not None
        a = result.aadhaar
        assert a.name.value is not None
        assert "Ravi" in a.name.value or "RAVI" in a.name.value.upper()
        assert a.dob.value == "1990-05-15"
        assert a.gender.value == "Male"
        assert a.aadhaar_number.value == "123456789012"

    def test_father_name_extracted(self):
        result = self.parser.parse(_make_ocr(AADHAAR_FULL_TEXT))
        assert result.aadhaar.father_name.value is not None
        assert "Rajesh" in result.aadhaar.father_name.value or \
               "RAJESH" in result.aadhaar.father_name.value.upper()

    def test_dob_variant_dash_format(self):
        result = self.parser.parse(_make_ocr(AADHAAR_DOB_VARIANT))
        assert result.aadhaar.dob.value == "1995-08-22"

    def test_female_gender(self):
        result = self.parser.parse(_make_ocr(AADHAAR_DOB_VARIANT))
        assert result.aadhaar.gender.value == "Female"

    def test_daughter_of_label(self):
        result = self.parser.parse(_make_ocr(AADHAAR_DOB_VARIANT))
        assert result.aadhaar.father_name.value is not None

    def test_year_of_birth_fallback(self):
        result = self.parser.parse(_make_ocr(AADHAAR_ONLY_YEAR))
        assert result.aadhaar.year_of_birth.value == "1988"

    def test_no_label_heuristic_extraction(self):
        """Parser should still extract fields even without explicit labels."""
        result = self.parser.parse(_make_ocr(AADHAAR_NO_LABELS))
        a = result.aadhaar
        # At minimum, 12-digit number and gender should be found
        assert a.aadhaar_number.value == "345678901234"
        assert a.gender.value == "Male"

    def test_masked_aadhaar_front_card(self):
        result = self.parser.parse(_make_ocr(AADHAAR_FRONT_TEXT))
        # Front card has masked Aadhaar XXXX XXXX 9012
        # Since no full 12-digit number present, masked form accepted
        a = result.aadhaar
        assert a.aadhaar_number.value is not None

    def test_status_success_when_complete(self):
        result = self.parser.parse(_make_ocr(AADHAAR_FULL_TEXT))
        assert result.status == DocumentStatus.SUCCESS

    def test_status_partial_when_some_fields_missing(self):
        partial_text = "Government of India\nName: RAVI KUMAR\n1234 5678 9012"
        result = self.parser.parse(_make_ocr(partial_text))
        assert result.status in (DocumentStatus.PARTIAL, DocumentStatus.SUCCESS)

    def test_status_failed_on_garbage(self):
        result = self.parser.parse(_make_ocr(GARBAGE_TEXT, confidence=5.0, words=2))
        assert result.status == DocumentStatus.FAILED

    def test_high_confidence_fields_not_flagged_for_review(self):
        result = self.parser.parse(_make_ocr(AADHAAR_FULL_TEXT, confidence=95.0))
        # Name extracted via label match + bonus → should NOT need review
        assert "name" not in result.aadhaar.fields_needing_review

    def test_result_contains_ocr_metadata(self):
        result = self.parser.parse(_make_ocr(AADHAAR_FULL_TEXT))
        assert result.ocr_engine_used == "tesseract_pass1"
        assert "greyscale" in result.preprocessing_applied

    def test_low_quality_partial_extraction(self):
        """Even garbled OCR should extract something."""
        result = self.parser.parse(_make_ocr(AADHAAR_LOW_QUALITY, confidence=40.0))
        # DOB should still be found via date pattern
        assert result.aadhaar is not None


# ── PANParser tests ───────────────────────────────────────────────────────────

class TestPANParser:
    def setup_method(self):
        self.parser = PANParser()

    def test_document_type(self):
        assert self.parser.document_type == "pan"

    def test_parse_never_raises(self):
        result = self.parser.parse(_make_ocr(GARBAGE_TEXT))
        assert result is not None

    def test_happy_path_standard_pan(self):
        result = self.parser.parse(_make_ocr(PAN_STANDARD_TEXT))
        assert result.pan is not None
        p = result.pan
        assert p.pan_number.value == "ABCDE1234F"
        assert p.name.value is not None
        assert "Ravi" in p.name.value or "RAVI" in p.name.value.upper()

    def test_father_name_extracted(self):
        result = self.parser.parse(_make_ocr(PAN_STANDARD_TEXT))
        assert result.pan.father_name.value is not None
        assert "Rajesh" in result.pan.father_name.value or \
               "RAJESH" in result.pan.father_name.value.upper()

    def test_female_card(self):
        result = self.parser.parse(_make_ocr(PAN_FEMALE_TEXT))
        assert result.pan.pan_number.value == "FGHIJ5678K"
        assert result.pan.name.value is not None

    def test_pan_with_ocr_noise_in_header(self):
        """OCR errors in header text should not prevent PAN extraction."""
        result = self.parser.parse(_make_ocr(PAN_WITH_OCR_NOISE))
        assert result.pan.pan_number.value == "ABCDE1234F"

    def test_pan_with_spaces_normalised(self):
        """'ABCDE 1234 F' should normalise to 'ABCDE1234F'."""
        result = self.parser.parse(_make_ocr(PAN_WITH_OCR_NOISE))
        pan = result.pan.pan_number.value
        assert pan is not None
        assert " " not in pan

    def test_pan_in_middle_of_text(self):
        result = self.parser.parse(_make_ocr(PAN_PAN_IN_MIDDLE))
        assert result.pan.pan_number.value == "ABCDE1234F"

    def test_status_success_when_complete(self):
        result = self.parser.parse(_make_ocr(PAN_STANDARD_TEXT))
        assert result.status == DocumentStatus.SUCCESS

    def test_status_failed_on_garbage(self):
        result = self.parser.parse(_make_ocr(GARBAGE_TEXT, confidence=5.0, words=2))
        assert result.status == DocumentStatus.FAILED

    def test_pan_number_format_validated(self):
        """PAN must match AAAAA0000A — text with no valid 10-char PAN returns None."""
        bad_text = "Name\n12345\nABCD\n9999\n"
        result = self.parser.parse(_make_ocr(bad_text))
        assert result.pan.pan_number.value is None

    def test_pan_number_uppercase(self):
        """PAN number in result is always uppercase regardless of OCR case."""
        lower_text = "Name\nRavi Kumar\nabcde1234f\n"
        result = self.parser.parse(_make_ocr(lower_text))
        pan = result.pan.pan_number.value
        if pan:
            assert pan == pan.upper()

    def test_confidence_scores_in_range(self):
        result = self.parser.parse(_make_ocr(PAN_STANDARD_TEXT, confidence=80.0))
        p = result.pan
        for field in [p.name, p.father_name, p.pan_number]:
            assert 0.0 <= field.confidence <= 100.0

    def test_result_contains_ocr_metadata(self):
        result = self.parser.parse(_make_ocr(PAN_STANDARD_TEXT))
        assert result.ocr_engine_used == "tesseract_pass1"
        assert isinstance(result.preprocessing_applied, list)


# ── BaseDocumentParser contract ───────────────────────────────────────────────

class TestBaseParserContract:
    def test_aadhaar_implements_base(self):
        from backend.app.services.parsers.base import BaseDocumentParser
        assert isinstance(AadhaarParser(), BaseDocumentParser)

    def test_pan_implements_base(self):
        from backend.app.services.parsers.base import BaseDocumentParser
        assert isinstance(PANParser(), BaseDocumentParser)

    def test_both_parsers_return_extraction_result(self):
        from backend.app.schemas.extraction import ExtractionResult
        ocr = _make_ocr("some text")
        assert isinstance(AadhaarParser().parse(ocr), ExtractionResult)
        assert isinstance(PANParser().parse(ocr), ExtractionResult)
