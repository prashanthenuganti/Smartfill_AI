"""
services/parsers/aadhaar_parser.py
-----------------------------------
Extracts structured fields from Aadhaar card OCR text.

Aadhaar card layout (standard UIDAI format):
┌──────────────────────────────────────┐
│  [UIDAI Logo]   भारत सरकार            │
│                 Government of India   │
│                                       │
│  [Photo]   Name: RAVI KUMAR           │
│            S/O: RAJESH KUMAR          │
│            DOB: 15/05/1990            │
│            Gender: Male               │
│                                       │
│  XXXX XXXX 9012   (front, masked)     │
│  1234 5678 9012   (back, full)        │
│  [QR Code]                            │
└──────────────────────────────────────┘

Parsing strategy:
  1. Split OCR text into cleaned lines.
  2. Label-based extraction first  (e.g. "DOB: 15/05/1990" → date after colon)
  3. Pattern-based fallback        (e.g. 12-digit number → Aadhaar number)
  4. Normalise via text_normalizer.
  5. Wrap in ExtractionField with confidence derived from OCR average.
"""

from __future__ import annotations

import re
from typing import Optional

from backend.app.core.logging import get_logger
from backend.app.schemas.extraction import (
    AadhaarExtraction,
    DocumentStatus,
    ExtractionField,
    ExtractionResult,
)
from backend.app.services.ocr.engine import OCRResult
from backend.app.services.parsers.base import BaseDocumentParser
from backend.app.utils.text_normalizer import (
    clean_ocr_line,
    extract_year,
    normalize_aadhaar,
    normalize_dob,
    normalize_gender,
    normalize_name,
)

logger = get_logger(__name__)

# ── Regex patterns ────────────────────────────────────────────────────────────

_RE_AADHAAR_RAW = re.compile(r"\b(\d{4}[\s\-]?\d{4}[\s\-]?\d{4})\b")
_RE_AADHAAR_MASKED = re.compile(r"\b([Xx*]{4}[\s\-]?[Xx*]{4}[\s\-]?\d{4})\b")
_RE_DOB_INLINE = re.compile(
    r"(?:DOB|Date\s*of\s*Birth|D\.O\.B)[:\s]+(\d{1,2}[/\-.\s]\d{1,2}[/\-.\s]\d{2,4})",
    re.IGNORECASE,
)
_RE_YEAR_INLINE = re.compile(
    r"(?:Year\s*of\s*Birth|YOB)[:\s]+(\d{4})", re.IGNORECASE
)
_RE_GENDER = re.compile(
    r"(?:Gender|Sex)[:\s]+(Male|Female|Other|M|F|पुरुष|महिला)", re.IGNORECASE
)
_RE_NAME_LABEL = re.compile(
    r"(?:^|\n)\s*(?:Name|नाम)[:\s]+(.+)", re.IGNORECASE | re.MULTILINE
)
_RE_FATHER_LABEL = re.compile(
    r"(?:S/O|D/O|W/O|C/O|Father|Husband)[:\s]+(.+)", re.IGNORECASE
)
_RE_DATE_PATTERN = re.compile(r"\d{1,2}[/\-.\s]\d{1,2}[/\-.\s]\d{2,4}")

_PATTERN_BONUS   =  5.0
_HEURISTIC_PENALTY = 10.0

_SKIP_KEYWORDS = frozenset([
    "government", "india", "uidai", "authority", "identification",
    "aadhaar", "आधार", "भारत", "unique", "male", "female",
])


class AadhaarParser(BaseDocumentParser):
    """Parses raw OCR text from an Aadhaar card into AadhaarExtraction."""

    @property
    def document_type(self) -> str:
        return "aadhaar"

    def parse(self, ocr_result: OCRResult) -> ExtractionResult:
        """Parse Aadhaar OCR text. Never raises."""
        logger.info("Parsing Aadhaar | words=%d | confidence=%.1f",
                    ocr_result.word_count, ocr_result.avg_confidence)
        try:
            extraction = self._extract_all(ocr_result)
        except Exception as exc:
            logger.error("Aadhaar parser crashed | error=%s", exc)
            return ExtractionResult(
                document_type=self.document_type,
                status=DocumentStatus.FAILED,
                error=f"Parser error: {exc}",
                avg_confidence=0.0,
            )

        if extraction.is_complete:
            status = DocumentStatus.SUCCESS
        elif any(f.value is not None for f in [
            extraction.name, extraction.dob, extraction.aadhaar_number
        ]):
            status = DocumentStatus.PARTIAL
        else:
            status = DocumentStatus.FAILED

        logger.info("Aadhaar parse complete | status=%s | review=%s",
                    status, extraction.fields_needing_review)

        return ExtractionResult(
            document_type=self.document_type,
            status=status,
            aadhaar=extraction,
            ocr_engine_used=ocr_result.engine,
            avg_confidence=ocr_result.avg_confidence,
            preprocessing_applied=ocr_result.preprocessing,
        )

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _extract_all(self, ocr_result: OCRResult) -> AadhaarExtraction:
        text = ocr_result.text
        base = ocr_result.avg_confidence
        lines = [c for line in text.splitlines() if (c := clean_ocr_line(line))]
        return AadhaarExtraction(
            name=self._extract_name(text, lines, base),
            father_name=self._extract_father_name(text, base),
            gender=self._extract_gender(text, lines, base),
            dob=self._extract_dob(text, lines, base),
            year_of_birth=self._extract_yob(text, base),
            aadhaar_number=self._extract_aadhaar_number(text, base),
        )

    def _make(self, value: Optional[str], conf: float, base: float,
              bonus: float = 0.0) -> ExtractionField:
        return ExtractionField.from_value(
            value, min(conf + bonus, 100.0), base
        )

    def _extract_name(self, text: str, lines: list[str], base: float) -> ExtractionField:
        """
        Extract cardholder name from Aadhaar card.

        Handles 3 Aadhaar formats:
          1. New format: "Name: Enuganti Kavya" (English label + value)
          2. New format: name printed in Title Case without label, before DOB line
          3. Old format: name printed in ALL CAPS
        """
        # Strategy 1 — explicit "Name:" label
        m = _RE_NAME_LABEL.search(text)
        if m:
            raw = re.split(r"\n|DOB|Gender|S/O|D/O", m.group(1), flags=re.IGNORECASE)[0]
            name = normalize_name(raw.strip())
            if name and len(name) > 2:
                return self._make(name, base, base, _PATTERN_BONUS)

        # Strategy 2 — new Aadhaar: title-case name appears before DOB line
        # Find the DOB line index as anchor
        dob_idx = next(
            (i for i, l in enumerate(lines)
             if re.search(r"DOB|Date of Birth|जन्म|పుట్టిన", l, re.IGNORECASE)),
            None
        )
        if dob_idx and dob_idx > 0:
            # Name is typically 1-3 lines before DOB
            for i in range(max(0, dob_idx - 3), dob_idx):
                line = lines[i]
                words = line.split()
                # Title-case words: first letter upper, rest lower (e.g. "Enuganti Kavya")
                alpha_words = [w for w in words if w.isalpha() and len(w) > 1]
                title_words = [w for w in alpha_words if w[0].isupper()]
                if (len(title_words) >= 2
                        and not any(kw in line.lower() for kw in _SKIP_KEYWORDS)
                        and not re.search(r"\d", line)):
                    name = normalize_name(" ".join(title_words))
                    if name and len(name) > 3:
                        return self._make(name, base, base, _PATTERN_BONUS)

        # Strategy 3 — look for substantial title-case name words in any line
        # Handles: "7 po fe Nd Prashanth Enuganti" → extract "Prashanth Enuganti"
        # Requires: ≥2 title-case words of length ≥5 (filters out OCR noise like "Nd")
        for line in lines:
            words = line.split()
            # Extract substantial title-case words (len>=5, starts uppercase, rest lowercase)
            _STRIP = '._,-'
            name_words = [
                w.strip(_STRIP) for w in words
                if len(w.strip(_STRIP)) >= 5
                and w.strip(_STRIP).isalpha()
                and w.strip(_STRIP)[0].isupper()
                and not w.strip(_STRIP).isupper()
                and w.strip(_STRIP).lower() not in _SKIP_KEYWORDS
            ]
            if len(name_words) >= 2:
                name = normalize_name(" ".join(name_words))
                if name and len(name) > 5:
                    return self._make(name, base - _HEURISTIC_PENALTY, base)

        # Strategy 4 — fallback: any 2-4 word alphabetic line
        for line in lines:
            words = line.split()
            alpha_words = [
                w.strip("._,-") for w in words
                if w.strip("._,-").replace(".", "").isalpha()
                and len(w.strip("._,-")) > 2
            ]
            if 2 <= len(alpha_words) <= 4:
                if not any(kw in line.lower() for kw in _SKIP_KEYWORDS):
                    name = normalize_name(" ".join(alpha_words))
                    if name and len(name) > 3:
                        return self._make(name, base - _HEURISTIC_PENALTY, base)

        return ExtractionField.empty()

    def _extract_father_name(self, text: str, base: float) -> ExtractionField:
        m = _RE_FATHER_LABEL.search(text)
        if m:
            raw = re.split(r"\n|DOB|Gender", m.group(1), flags=re.IGNORECASE)[0]
            name = normalize_name(raw.strip())
            if name:
                return self._make(name, base, base, _PATTERN_BONUS)
        return ExtractionField.empty()

    def _extract_gender(self, text: str, lines: list[str], base: float) -> ExtractionField:
        m = _RE_GENDER.search(text)
        if m:
            g = normalize_gender(m.group(1))
            if g:
                return self._make(g, base, base, _PATTERN_BONUS)
        for line in lines:
            g = normalize_gender(line.strip())
            if g in ("Male", "Female"):
                return self._make(g, base - _HEURISTIC_PENALTY, base)
        return ExtractionField.empty()

    def _extract_dob(self, text: str, lines: list[str], base: float) -> ExtractionField:
        m = _RE_DOB_INLINE.search(text)
        if m:
            dob = normalize_dob(m.group(1))
            if dob:
                return self._make(dob, base, base, _PATTERN_BONUS)
        for line in lines:
            dm = _RE_DATE_PATTERN.search(line)
            if dm:
                dob = normalize_dob(dm.group(0))
                if dob:
                    return self._make(dob, base - _HEURISTIC_PENALTY, base)
        return ExtractionField.empty()

    def _extract_yob(self, text: str, base: float) -> ExtractionField:
        m = _RE_YEAR_INLINE.search(text)
        if m:
            return self._make(m.group(1), base, base, _PATTERN_BONUS)
        year = extract_year(text)
        if year:
            return self._make(year, base - _HEURISTIC_PENALTY, base)
        return ExtractionField.empty()

    def _extract_aadhaar_number(self, text: str, base: float) -> ExtractionField:
        for m in _RE_AADHAAR_RAW.finditer(text):
            n = normalize_aadhaar(m.group(1))
            if n and len(n) == 12:
                return self._make(n, base, base, _PATTERN_BONUS)
        m = _RE_AADHAAR_MASKED.search(text)
        if m:
            n = normalize_aadhaar(m.group(1))
            if n:
                return self._make(n, base - _HEURISTIC_PENALTY, base)
        return ExtractionField.empty()
