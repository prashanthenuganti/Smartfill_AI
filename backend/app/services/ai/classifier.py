"""
services/ai/classifier.py
--------------------------
Keyword classifier — fast fallback when Vision LLM classify() fails.
Used as Stage 1 Tesseract fallback in orchestrator._quick_classify().

Note: For coloured/busy documents (PAN cards, etc.) Tesseract OCR will
be garbled and this will return AUTO. That's fine — Vision LLM classify()
runs first and handles those cases correctly.
"""
from __future__ import annotations
import re
from backend.app.core.logging import get_logger
from backend.app.schemas.documents import DocumentType

logger = get_logger(__name__)

# ── Signatures: (DocumentType, required_keywords, optional_patterns) ──────────
# required_keywords: ALL must be present (case-insensitive)
# optional_patterns: regex patterns that boost confidence

_SIGNATURES: list[tuple[DocumentType, list[str], list[str]]] = [

    # ── Aadhaar ───────────────────────────────────────────────────────────────
    (DocumentType.AADHAAR, ["uidai", "aadhaar"], [r"\b\d{4}\s?\d{4}\s?\d{4}\b"]),
    (DocumentType.AADHAAR, ["unique identification authority", "india"], [r"\b\d{12}\b"]),
    (DocumentType.AADHAAR, ["government of india", "aadhaar"], []),

    # ── PAN ───────────────────────────────────────────────────────────────────
    (DocumentType.PAN, ["income tax", "permanent account"], [r"[A-Z]{5}[0-9]{4}[A-Z]"]),
    (DocumentType.PAN, ["income tax department", "pan"], []),

    # ── Passport ──────────────────────────────────────────────────────────────
    (DocumentType.PASSPORT, ["republic of india", "passport"], [r"[A-Z]\d{7}", r"place of birth"]),
    (DocumentType.PASSPORT, ["passport", "date of expiry", "nationality"], []),

    # ── Driving Licence ───────────────────────────────────────────────────────
    (DocumentType.DRIVING_LICENSE, ["driving licence", "transport"], [r"[A-Z]{2}\d{2}"]),
    (DocumentType.DRIVING_LICENSE, ["motor vehicles act", "licence"], [r"valid till|date of issue"]),

    # ── Voter ID ──────────────────────────────────────────────────────────────
    (DocumentType.VOTER_ID, ["election commission", "elector"], []),
    (DocumentType.VOTER_ID, ["photo identity card", "constituency"], []),

    # ── SSC ───────────────────────────────────────────────────────────────────
    (DocumentType.CERTIFICATE_SSC, ["board of secondary education"], [r"hall ticket|ssc"]),
    (DocumentType.CERTIFICATE_SSC, ["board of secondary", "ssc"], []),
    (DocumentType.CERTIFICATE_SSC, ["secondary school certificate"], []),
    (DocumentType.CERTIFICATE_SSC, ["bseap", "10th class"], [r"hall ticket"]),
    (DocumentType.CERTIFICATE_SSC, ["cbse", "matriculation"], [r"hall ticket|roll number"]),

    # ── Intermediate ──────────────────────────────────────────────────────────
    (DocumentType.CERTIFICATE_INTER, ["board of intermediate", "intermediate"], [r"hall ticket"]),
    (DocumentType.CERTIFICATE_INTER, ["bieap", "junior college", "intermediate education"], []),
    (DocumentType.CERTIFICATE_INTER, ["mpc", "bipc", "cec", "mec", "11th", "12th"], [r"hall ticket|college"]),

    # ── Degree Certificate / Marks Memo ───────────────────────────────────────
    # Broad match: any university + bachelor/master/degree
    (DocumentType.CERTIFICATE_DEGREE, ["bachelor of technology", "university"], []),
    (DocumentType.CERTIFICATE_DEGREE, ["bachelor of engineering", "university"], []),
    (DocumentType.CERTIFICATE_DEGREE, ["bachelor of science", "university"], []),
    (DocumentType.CERTIFICATE_DEGREE, ["bachelor of commerce", "university"], []),
    (DocumentType.CERTIFICATE_DEGREE, ["master of technology", "university"], []),
    (DocumentType.CERTIFICATE_DEGREE, ["master of science", "university"], []),
    (DocumentType.CERTIFICATE_DEGREE, ["consolidated marks memo", "university"], []),
    (DocumentType.CERTIFICATE_DEGREE, ["marks memo", "college of engineering"], []),
    (DocumentType.CERTIFICATE_DEGREE, ["credit sheet", "cgpa"], [r"hall ticket|cmm no"]),
    (DocumentType.CERTIFICATE_DEGREE, ["provisional certificate", "university", "degree"], []),
    (DocumentType.CERTIFICATE_DEGREE, ["convocation", "university", "bachelor"], []),
    # JNTU / Osmania / specific universities
    (DocumentType.CERTIFICATE_DEGREE, ["jawaharlal nehru technological university", "bachelor"], []),
    (DocumentType.CERTIFICATE_DEGREE, ["osmania university", "bachelor"], []),
    (DocumentType.CERTIFICATE_DEGREE, ["autonomous", "college of engineering", "cgpa"], []),

    # ── Bank Passbook ─────────────────────────────────────────────────────────
    (DocumentType.BANK_PASSBOOK, ["savings account", "ifsc"], [r"[A-Z]{4}0[A-Z0-9]{6}"]),
    (DocumentType.BANK_PASSBOOK, ["passbook", "account number"], []),
    (DocumentType.BANK_PASSBOOK, ["current account", "ifsc", "bank"], []),

    # ── Salary Slip ───────────────────────────────────────────────────────────
    (DocumentType.SALARY_SLIP, ["salary slip", "basic pay"], []),
    (DocumentType.SALARY_SLIP, ["gross salary", "net salary", "employee"], []),
    (DocumentType.SALARY_SLIP, ["provident fund", "deductions", "net pay"], []),
]


class DocumentClassifier:

    def classify(self, ocr_text: str) -> tuple[DocumentType, float]:
        text_lower = ocr_text.lower()
        best_type  = DocumentType.AUTO
        best_score = 0.0

        for doc_type, required_kws, optional_patterns in _SIGNATURES:
            if not all(kw.lower() in text_lower for kw in required_kws):
                continue

            # Base score: more required keywords = more certain
            score = 0.5 + (len(required_kws) * 0.1)

            # Optional pattern boost
            for pattern in optional_patterns:
                if re.search(pattern, ocr_text, re.IGNORECASE):
                    score += 0.15

            score = min(score, 1.0)

            if score > best_score:
                best_score = score        # ← was "best_score = best_type" — bug fixed
                best_type  = doc_type

        # Pattern-only fallback
        if best_type == DocumentType.AUTO:
            best_type, best_score = self._pattern_fallback(ocr_text)

        logger.info("Classified | type=%s | confidence=%.2f", best_type.value, best_score)
        return best_type, best_score

    def _pattern_fallback(self, ocr_text: str) -> tuple[DocumentType, float]:
        if re.search(r'\b[A-Z]{5}[0-9]{4}[A-Z]\b', ocr_text):
            return DocumentType.PAN, 0.7
        if re.search(r'\b\d{4}\s?\d{4}\s?\d{4}\b', ocr_text):
            return DocumentType.AADHAAR, 0.6
        if re.search(r'\b[A-Z]{4}0[A-Z0-9]{6}\b', ocr_text):
            return DocumentType.BANK_PASSBOOK, 0.5
        return DocumentType.AUTO, 0.0
