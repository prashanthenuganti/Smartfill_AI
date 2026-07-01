"""
services/ai/extractor.py
-------------------------
AI Understanding Layer — converts raw OCR text to structured JSON.

Uses Claude Haiku (claude-haiku-4-5) which is:
  - Cheapest Anthropic model with strong JSON output
  - No image tokens needed (we send OCR TEXT, not the image)
  - ~₹0.01 per document at typical token counts
  - Fast: 1-2 second response time

This layer is called when:
  1. OCR confidence < OCR_TIER3_CONFIDENCE (50% default)
  2. Required fields are missing after parser runs
  3. Document type is not Aadhaar/PAN (no parser exists)

The AI receives:
  - Document type (for correct field spec)
  - Raw OCR text (not the image — avoids image token costs)
  - Field extraction prompt

The AI returns structured JSON which is converted to
DocumentExtraction with per-field ExtractionField objects.
"""

from __future__ import annotations

import json
import re
import time
from typing import Optional

import anthropic

from backend.app.core.config import get_settings
from backend.app.core.exceptions import OCRError
from backend.app.core.logging import get_logger
from backend.app.prompts.extraction import get_extraction_prompt
from backend.app.schemas.extraction import DocumentExtraction, ExtractionField

logger = get_logger(__name__)

_client: Optional[anthropic.Anthropic] = None


def _get_client() -> anthropic.Anthropic:
    """Lazy-initialise Anthropic client."""
    global _client
    if _client is None:
        settings = get_settings()
        if not settings.has_anthropic_key:
            raise OCRError(
                "ANTHROPIC_API_KEY not configured. "
                "Add it to your .env file to enable AI extraction."
            )
        _client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    return _client


class AIExtractor:
    """
    Converts raw OCR text to structured document fields using Haiku.
    """

    def __init__(self) -> None:
        self._settings = get_settings()

    def is_available(self) -> bool:
        """Check if AI extraction is configured and enabled."""
        return (
            self._settings.ai_extraction_enabled
            and self._settings.has_anthropic_key
        )

    def extract(
        self,
        document_type: str,
        ocr_text: str,
        ocr_confidence: float = 0.0,
    ) -> DocumentExtraction:
        """
        Extract structured fields from OCR text using Haiku.

        Args:
            document_type:  DocumentType string value
            ocr_text:       Raw OCR text from Tesseract or Surya
            ocr_confidence: OCR confidence (used for field confidence baseline)

        Returns:
            DocumentExtraction with populated fields dict.

        Raises:
            OCRError: If API call fails or returns invalid JSON.
        """
        logger.info(
            "AI extraction started | type=%s | text_len=%d | ocr_conf=%.1f%%",
            document_type, len(ocr_text), ocr_confidence,
        )
        start_ms = time.monotonic() * 1000

        prompt = get_extraction_prompt(document_type, ocr_text)

        try:
            client = _get_client()
            response = client.messages.create(
                model=self._settings.ai_model,
                max_tokens=self._settings.ai_max_tokens,
                messages=[{"role": "user", "content": prompt}],
            )
        except anthropic.AuthenticationError:
            raise OCRError("Invalid ANTHROPIC_API_KEY. Check your .env file.")
        except anthropic.RateLimitError:
            raise OCRError("Anthropic rate limit reached. Retry in a moment.")
        except anthropic.APIConnectionError as exc:
            raise OCRError(f"Cannot reach Anthropic API: {exc}")
        except Exception as exc:
            raise OCRError(f"AI extraction API error: {exc}") from exc

        raw_response = response.content[0].text if response.content else ""
        elapsed_ms = (time.monotonic() * 1000) - start_ms

        logger.info(
            "AI extraction complete | time=%.0fms | tokens_in=%d | tokens_out=%d",
            elapsed_ms,
            response.usage.input_tokens,
            response.usage.output_tokens,
        )

        # Parse the JSON response
        extraction = self._parse_response(raw_response, document_type, ocr_confidence)
        extraction.raw_text = ocr_text
        extraction.extraction_method = "ai"

        return extraction

    # ── Private helpers ───────────────────────────────────────────────────────

    def _parse_response(
        self,
        raw: str,
        document_type: str,
        ocr_confidence: float,
    ) -> DocumentExtraction:
        """
        Parse Haiku's JSON response into a DocumentExtraction.

        Haiku should return pure JSON, but we strip any accidental
        markdown fences just in case.
        """
        # Strip markdown code blocks if present
        clean = re.sub(r"```(?:json)?\s*", "", raw).strip()
        clean = clean.strip("`").strip()

        try:
            data = json.loads(clean)
        except json.JSONDecodeError as exc:
            logger.error(
                "AI returned invalid JSON | error=%s | raw=%s",
                exc, raw[:200]
            )
            # Return empty extraction rather than crashing
            return DocumentExtraction(
                document_type=document_type,
                fields={},
                ocr_engine="ai_failed",
                avg_confidence=0.0,
            )

        # Convert the flat dict to ExtractionField objects
        fields: dict[str, ExtractionField] = {}
        total_conf = 0.0
        field_count = 0

        for field_name, field_data in data.items():
            if not isinstance(field_data, dict):
                continue

            value = field_data.get("value")
            confidence = float(field_data.get("confidence", 0))

            # Normalise value
            if value is not None:
                value = str(value).strip()
                if not value or value.lower() in ("null", "none", "n/a", "-"):
                    value = None
                    confidence = 0.0

            fields[field_name] = ExtractionField.from_value(
                value=value,
                confidence=confidence,
                threshold=60.0,
            )

            total_conf += confidence
            field_count += 1

        avg_conf = total_conf / field_count if field_count > 0 else 0.0

        return DocumentExtraction(
            document_type=document_type,
            fields=fields,
            ocr_engine="haiku",
            avg_confidence=round(avg_conf, 1),
            extraction_method="ai",
        )
