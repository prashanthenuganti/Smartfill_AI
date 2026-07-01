"""
services/parsers/pan_parser.py
--------------------------------
Extracts structured fields from PAN card OCR text.

Fields extracted:
  - name         : cardholder name
  - father_name  : father's name
  - dob          : date of birth (normalised YYYY-MM-DD)
  - pan_number   : PAN number (AAAAA0000A format)

Layout awareness:
  - PAN number appears in the middle of the card
  - Labels are bilingual Hindi+English
  - Name appears immediately before father's name label
  - DOB appears after father's name, near bottom
"""

from __future__ import annotations

import re
from typing import Optional

from backend.app.core.logging import get_logger
from backend.app.schemas.extraction import (
    DocumentStatus,
    ExtractionField,
    ExtractionResult,
    PANExtraction,
)
from backend.app.services.ocr.engine import OCRResult
from backend.app.services.parsers.base import BaseDocumentParser
from backend.app.utils.text_normalizer import (
    clean_ocr_line,
    normalize_dob,
    normalize_name,
    normalize_pan,
)

logger = get_logger(__name__)

# ── Regex patterns ────────────────────────────────────────────────────────────

_RE_PAN_STRICT = re.compile(r'\b([A-Z]{5}[0-9]{4}[A-Z])\b')

_RE_NAME_LABEL = re.compile(
    r'(?:नाम\s*/\s*Name|Name\s*/|^Name$)',
    re.IGNORECASE | re.MULTILINE,
)
_RE_FATHER_LABEL = re.compile(
    r'(?:पिता|Father)',
    re.IGNORECASE,
)
_RE_DOB_LABEL = re.compile(
    r'(?:जन्म|Date\s*of\s*Birth|DOB|taarikh|after)',
    re.IGNORECASE,
)
_RE_DATE = re.compile(r'\b(\d{1,2}[/\-\.]\d{1,2}[/\-\.]\d{2,4})\b')

_SKIP_KEYWORDS = frozenset([
    'income', 'tax', 'department', 'govt', 'government', 'india',
    'permanent', 'account', 'number', 'signature', 'date', 'birth',
    'father', 'name', 'pan', 'card', 'हस्ताक्षर', 'आयकर', 'विभाग',
    'भारत', 'सरकार', 'स्थायी', 'लेखा', 'संख्या',
])

_PATTERN_BONUS    =  5.0
_HEURISTIC_PENALTY = 10.0


class PANParser(BaseDocumentParser):

    @property
    def document_type(self) -> str:
        return "pan"

    def parse(self, ocr_result: OCRResult) -> ExtractionResult:
        logger.info("Parsing PAN | words=%d | confidence=%.1f",
                    ocr_result.word_count, ocr_result.avg_confidence)
        try:
            extraction = self._extract_all(ocr_result)
        except Exception as exc:
            logger.error("PAN parser crashed | error=%s", exc)
            return ExtractionResult(
                document_type=self.document_type,
                status=DocumentStatus.FAILED,
                error=f"Parser error: {exc}",
                avg_confidence=0.0,
            )

        if extraction.is_complete:
            status = DocumentStatus.SUCCESS
        elif extraction.pan_number.value or extraction.name.value:
            status = DocumentStatus.PARTIAL
        else:
            status = DocumentStatus.FAILED

        logger.info("PAN parse complete | status=%s | review=%s",
                    status, extraction.fields_needing_review)

        return ExtractionResult(
            document_type=self.document_type,
            status=status,
            pan=extraction,
            ocr_engine_used=ocr_result.engine,
            avg_confidence=ocr_result.avg_confidence,
            preprocessing_applied=ocr_result.preprocessing,
        )

    def _extract_all(self, ocr_result: OCRResult) -> PANExtraction:
        text = ocr_result.text
        base = ocr_result.avg_confidence
        lines = [c for line in text.splitlines() if (c := clean_ocr_line(line))]
        return PANExtraction(
            name=self._extract_name(text, lines, base),
            father_name=self._extract_father_name(text, lines, base),
            dob=self._extract_dob(text, lines, base),
            pan_number=self._extract_pan_number(text, base),
        )

    def _make(self, value: Optional[str], conf: float, base: float,
              bonus: float = 0.0) -> ExtractionField:
        return ExtractionField.from_value(value, min(conf + bonus, 100.0), base)

    # ── PAN Number ────────────────────────────────────────────────────────────

    def _extract_pan_number(self, text: str, base: float) -> ExtractionField:
        cleaned = re.sub(r'\s+', '', text.upper())
        m = _RE_PAN_STRICT.search(cleaned)
        if m:
            return self._make(m.group(1), base, base, _PATTERN_BONUS)
        pan = normalize_pan(text)
        if pan:
            return self._make(pan, base - _HEURISTIC_PENALTY, base)
        return ExtractionField.empty()

    # ── Name ─────────────────────────────────────────────────────────────────

    def _extract_name(self, text: str, lines: list[str], base: float) -> ExtractionField:
        """
        Strategy 1: bilingual label line → next line is the name.
        Strategy 2: walk backwards from father label → line immediately above.
        Strategy 3: first all-caps name line in document.
        """
        # Strategy 1: label match
        for i, line in enumerate(lines):
            if _RE_NAME_LABEL.search(line) and i + 1 < len(lines):
                candidate = lines[i + 1]
                if not _RE_FATHER_LABEL.search(candidate) and not _RE_DOB_LABEL.search(candidate):
                    name = self._clean_name_line(candidate)
                    if name and len(name) > 3 and not any(k in candidate.lower() for k in _SKIP_KEYWORDS):
                        return self._make(name, base, base, _PATTERN_BONUS)

        # Strategy 2: line immediately above father label
        father_label_idx = next(
            (i for i, l in enumerate(lines) if _RE_FATHER_LABEL.search(l)),
            None
        )
        if father_label_idx is not None and father_label_idx > 0:
            for i in range(father_label_idx - 1, max(0, father_label_idx - 5), -1):
                line = lines[i]
                upper_words = [
                    w for w in line.split()
                    if w.isalpha() and w.isupper() and len(w) >= 3
                    and w.lower() not in _SKIP_KEYWORDS
                ]
                if len(upper_words) >= 2:
                    name = normalize_name(" ".join(upper_words))
                    if name and len(name) > 3:
                        return self._make(name, base - _HEURISTIC_PENALTY, base)

        # Strategy 3: first clean all-caps line
        for line in lines:
            words = line.split()
            if (2 <= len(words) <= 5
                    and all(w.isalpha() and w.isupper() for w in words)
                    and not any(k in line.lower() for k in _SKIP_KEYWORDS)):
                name = normalize_name(line)
                if name:
                    return self._make(name, base - _HEURISTIC_PENALTY, base)

        return ExtractionField.empty()

    # ── Father Name ───────────────────────────────────────────────────────────

    def _extract_father_name(
        self, text: str, lines: list[str], base: float
    ) -> ExtractionField:
        """
        Strategy 1: father label line → next line is the name.
        Strategy 2: second all-caps name line in document.
        """
        # Strategy 1: label match
        for i, line in enumerate(lines):
            if _RE_FATHER_LABEL.search(line) and i + 1 < len(lines):
                candidate = lines[i + 1]
                if not _RE_DOB_LABEL.search(candidate):
                    upper_words = [
                        w for w in candidate.split()
                        if w.isalpha() and w.isupper() and len(w) >= 2
                    ]
                    if len(upper_words) >= 2:
                        name = normalize_name(" ".join(upper_words))
                        if name and len(name) > 3:
                            return self._make(name, base, base, _PATTERN_BONUS)

        # Strategy 2: second all-caps name line
        name_lines_found = 0
        for line in lines:
            words = line.split()
            if (2 <= len(words) <= 6
                    and all(w.isalpha() and w.isupper() for w in words)
                    and not any(k in line.lower() for k in _SKIP_KEYWORDS)):
                name_lines_found += 1
                if name_lines_found == 2:
                    name = normalize_name(line)
                    if name:
                        return self._make(name, base - _HEURISTIC_PENALTY, base)

        return ExtractionField.empty()

    # ── Date of Birth ─────────────────────────────────────────────────────────

    def _extract_dob(self, text: str, lines: list[str], base: float) -> ExtractionField:
        """
        Strategy 1: find DOB label line → look for date on same or next line.
        Strategy 2: scan all lines for a date pattern after the father name.
        Strategy 3: any date in the full text (last resort).

        OCR often garbles the year (e.g. 2003 → 2069). We validate the year
        is in a realistic birth year range (1900–2015) and reject garbage.
        """
        # Strategy 1: DOB label → date on same or next line
        for i, line in enumerate(lines):
            if _RE_DOB_LABEL.search(line):
                # Check same line first
                dm = _RE_DATE.search(line)
                if dm:
                    dob = self._validate_dob(dm.group(1))
                    if dob:
                        return self._make(dob, base, base, _PATTERN_BONUS)
                # Check next line
                if i + 1 < len(lines):
                    dm = _RE_DATE.search(lines[i + 1])
                    if dm:
                        dob = self._validate_dob(dm.group(1))
                        if dob:
                            return self._make(dob, base, base, _PATTERN_BONUS)

        # Strategy 2: any date after father_name in line order
        father_label_idx = next(
            (i for i, l in enumerate(lines) if _RE_FATHER_LABEL.search(l)),
            0
        )
        for line in lines[father_label_idx:]:
            dm = _RE_DATE.search(line)
            if dm:
                dob = self._validate_dob(dm.group(1))
                if dob:
                    return self._make(dob, base - _HEURISTIC_PENALTY, base)

        # Strategy 3: any date anywhere — last resort
        for dm in _RE_DATE.finditer(text):
            dob = self._validate_dob(dm.group(1))
            if dob:
                return self._make(dob, base - _HEURISTIC_PENALTY * 2, base)

        return ExtractionField.empty()

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _clean_name_line(self, line: str) -> Optional[str]:
        """
        Extract only substantial uppercase words from a noisy OCR line.
        e.g. 'ENUGANTI KAVYA . ee ee' → 'Enuganti Kavya'
        """
        upper_words = [
            w for w in line.split()
            if w.isalpha() and w.isupper() and len(w) >= 3
            and w.lower() not in _SKIP_KEYWORDS
        ]
        if not upper_words:
            return None
        return normalize_name(" ".join(upper_words))

    def _validate_dob(self, raw_date: str) -> Optional[str]:
        """
        Normalise and validate a date string.
        Rejects years outside 1900–2015 (catches OCR garble like 2069).
        Also corrects common OCR year garbles for Indian DOBs.
        """
        from backend.app.utils.text_normalizer import normalize_dob
        import re

        # OCR sometimes reads digits wrong in years
        # Fix common garbles: 2069→2003, 2063→2003, 1990s garbles
        raw_date = re.sub(r'\b20[6-9]\d\b', lambda m: self._fix_year(m.group()), raw_date)

        dob = normalize_dob(raw_date)
        if not dob:
            return None

        # Validate year is realistic for a PAN card holder
        year = int(dob[:4])
        if not (1900 <= year <= 2015):
            return None

        return dob

    def _fix_year(self, garbled_year: str) -> str:
        """
        OCR digit corrections for years.

        Common misreads on PAN cards:
          0 → 6  (zero looks like six in some fonts)
          3 → 9  (three looks like nine when ink bleeds)
          e.g. 2003 → 2069, 1993 → 1969, 2000 → 2660

        Strategy: if the year is in an impossible range (2050-2099),
        apply digit-level corrections using the OCR correction map.
        """
        _DIGIT_FIX = {"6": "0", "9": "3", "8": "0", "5": "5"}
        try:
            y = int(garbled_year)
            if 2050 <= y <= 2099:
                # Fix digit by digit: 2069 → correct each suspicious digit
                digits = list(garbled_year)
                # Year 20xx — fix last two digits
                if digits[0] == "2" and digits[1] == "0":
                    digits[2] = _DIGIT_FIX.get(digits[2], digits[2])
                    digits[3] = _DIGIT_FIX.get(digits[3], digits[3])
                fixed = int("".join(digits))
                if 1900 <= fixed <= 2015:
                    return str(fixed)
            if 1950 <= y <= 1999:
                # Also check 19xx garbles
                digits = list(garbled_year)
                digits[2] = _DIGIT_FIX.get(digits[2], digits[2])
                digits[3] = _DIGIT_FIX.get(digits[3], digits[3])
                fixed = int("".join(digits))
                if 1900 <= fixed <= 2015:
                    return str(fixed)
        except ValueError:
            pass
        return garbled_year
