"""
services/ocr/vision_engine.py
-------------------------------
Two-stage Vision LLM pipeline:
  Stage 1: classify(image)  → document type string  (~₹0.001)
  Stage 2: extract(image, type) → structured JSON   (~₹0.08)

Two engines, identical interface:
  ClaudeVisionEngine  — claude-haiku-4-5  (testing)
  GeminiVisionEngine  — gemini-2.5-flash  (production)

Switch with VISION_ENGINE=claude|gemini in .env
"""
from __future__ import annotations
import base64, json, re, time
from dataclasses import dataclass, field
from typing import Optional

from backend.app.core.config import get_settings
from backend.app.core.logging import get_logger
from backend.app.prompts.gemini_prompts import CLASSIFY_PROMPT, get_prompt
from backend.app.schemas.extraction import DocumentExtraction, ExtractionField

logger = get_logger(__name__)

VALID_TYPES = {
    "aadhaar","pan","passport","driving_license","voter_id",
    "certificate_ssc","certificate_inter","certificate_degree",
    "bank_passbook","salary_slip",
}


@dataclass
class VisionResult:
    fields: dict[str, ExtractionField]
    document_type: str
    engine: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: float = 0.0
    raw_json: str = ""
    error: Optional[str] = None

    @property
    def success(self) -> bool:
        return self.error is None and bool(self.fields)

    def to_document_extraction(self) -> DocumentExtraction:
        return DocumentExtraction(
            document_type=self.document_type,
            fields=self.fields,
            ocr_engine=f"{self.engine}:{self.model}",
            avg_confidence=self._avg_confidence(),
            extraction_method="vision_llm",
        )

    def _avg_confidence(self) -> float:
        confs = [f.confidence for f in self.fields.values() if f.value]
        return round(sum(confs) / len(confs), 1) if confs else 0.0


class ClaudeVisionEngine:
    MODEL = "claude-haiku-4-5"

    def __init__(self) -> None:
        self._settings = get_settings()

    def is_available(self) -> bool:
        return bool(self._settings.anthropic_api_key)

    def classify(self, jpeg_bytes: bytes) -> str:
        """Stage 1: classify document type. Returns type string like 'pan', 'aadhaar'."""
        import anthropic
        try:
            client = anthropic.Anthropic(api_key=self._settings.anthropic_api_key)
            b64 = base64.standard_b64encode(jpeg_bytes).decode()
            response = client.messages.create(
                model=self.MODEL,
                max_tokens=20,
                messages=[{"role": "user", "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b64}},
                    {"type": "text", "text": CLASSIFY_PROMPT},
                ]}],
            )
            result = response.content[0].text.strip().lower().split()[0] if response.content else ""
            detected = result if result in VALID_TYPES else "unknown"
            logger.info("Claude classify | detected=%s", detected)
            return detected
        except Exception as exc:
            logger.error("Claude classify failed | %s", exc)
            return "unknown"

    def extract(self, jpeg_bytes: bytes, document_type: str) -> VisionResult:
        """Stage 2: extract all fields using document-specific prompt."""
        import anthropic
        start = time.monotonic()
        prompt = get_prompt(document_type)
        try:
            client = anthropic.Anthropic(api_key=self._settings.anthropic_api_key)
            b64 = base64.standard_b64encode(jpeg_bytes).decode()
            response = client.messages.create(
                model=self.MODEL,
                max_tokens=1024,
                messages=[{"role": "user", "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b64}},
                    {"type": "text", "text": prompt},
                ]}],
            )
            raw = response.content[0].text if response.content else ""
            latency = (time.monotonic() - start) * 1000
            logger.info("Claude extract | type=%s | tokens=%d+%d | %.0fms",
                document_type, response.usage.input_tokens, response.usage.output_tokens, latency)
            fields = _parse_json(raw)
            return VisionResult(
                fields=fields, document_type=document_type,
                engine="claude", model=self.MODEL,
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
                latency_ms=round(latency, 1), raw_json=raw,
            )
        except Exception as exc:
            logger.error("Claude extract failed | %s", exc)
            return VisionResult(fields={}, document_type=document_type,
                engine="claude", model=self.MODEL,
                latency_ms=(time.monotonic()-start)*1000, error=str(exc))


class GeminiVisionEngine:
    MODEL = "gemini-2.5-flash"

    def __init__(self) -> None:
        self._settings = get_settings()

    def is_available(self) -> bool:
        return bool(getattr(self._settings, "gemini_api_key", None))

    def classify(self, jpeg_bytes: bytes) -> str:
        """Stage 1: classify document type using Gemini Vision."""
        from google import genai
        from google.genai import types
        try:
            client = genai.Client(api_key=self._settings.gemini_api_key)
            response = client.models.generate_content(
                model=self.MODEL,
                contents=[
                    types.Part.from_bytes(data=jpeg_bytes, mime_type="image/jpeg"),
                    types.Part.from_text(text=CLASSIFY_PROMPT),
                ],
                config=types.GenerateContentConfig(temperature=0.0, max_output_tokens=20),
            )
            result = (response.text or "").strip().lower().split()[0]
            detected = result if result in VALID_TYPES else "unknown"
            logger.info("Gemini classify | detected=%s", detected)
            return detected
        except Exception as exc:
            logger.error("Gemini classify failed | %s", exc)
            return "unknown"

    def extract(self, jpeg_bytes: bytes, document_type: str) -> VisionResult:
        """Stage 2: extract all fields using document-specific prompt."""
        from google import genai
        from google.genai import types
        start = time.monotonic()
        prompt = get_prompt(document_type)
        try:
            client = genai.Client(api_key=self._settings.gemini_api_key)
            response = client.models.generate_content(
                model=self.MODEL,
                contents=[
                    types.Part.from_bytes(data=jpeg_bytes, mime_type="image/jpeg"),
                    types.Part.from_text(text=prompt),
                ],
                config=types.GenerateContentConfig(temperature=0.1, max_output_tokens=1024),
            )
            raw = response.text or ""
            latency = (time.monotonic() - start) * 1000
            usage = response.usage_metadata
            logger.info("Gemini extract | type=%s | tokens=%d+%d | %.0fms",
                document_type, getattr(usage,"prompt_token_count",0),
                getattr(usage,"candidates_token_count",0), latency)
            fields = _parse_json(raw)
            return VisionResult(
                fields=fields, document_type=document_type,
                engine="gemini", model=self.MODEL,
                input_tokens=getattr(usage,"prompt_token_count",0),
                output_tokens=getattr(usage,"candidates_token_count",0),
                latency_ms=round(latency,1), raw_json=raw,
            )
        except Exception as exc:
            logger.error("Gemini extract failed | %s", exc)
            return VisionResult(fields={}, document_type=document_type,
                engine="gemini", model=self.MODEL,
                latency_ms=(time.monotonic()-start)*1000, error=str(exc))


def _parse_json(raw: str) -> dict[str, ExtractionField]:
    clean = re.sub(r"```(?:json)?\s*", "", raw).strip().strip("`").strip()
    match = re.search(r"\{.*\}", clean, re.DOTALL)
    if match:
        clean = match.group()
    try:
        data = json.loads(clean)
    except json.JSONDecodeError:
        logger.warning("JSON parse failed | raw=%s", raw[:200])
        return {}
    fields: dict[str, ExtractionField] = {}
    for name, val in data.items():
        if not isinstance(val, dict):
            continue
        value = val.get("value")
        confidence = float(val.get("confidence", 0))
        if value is not None:
            value = str(value).strip()
            if not value or value.lower() in ("null","none","n/a","-",""):
                value = None
                confidence = 0.0
        fields[name] = ExtractionField.from_value(value=value, confidence=confidence, threshold=60.0)
    return fields


def get_vision_engine() -> ClaudeVisionEngine | GeminiVisionEngine:
    settings = get_settings()
    engine_name = getattr(settings, "vision_engine", "claude").lower()
    if engine_name == "gemini":
        engine = GeminiVisionEngine()
        if engine.is_available():
            logger.info("Vision engine: Gemini 2.5 Flash")
            return engine
        logger.warning("Gemini key not set, falling back to Claude")
    engine = ClaudeVisionEngine()
    logger.info("Vision engine: Claude Haiku 4.5 (testing)")
    return engine
