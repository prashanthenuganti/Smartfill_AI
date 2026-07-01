"""
services/pipeline/orchestrator.py
-----------------------------------
Milestone 2: Vision LLM pipeline orchestrator.

Architecture:
  Upload → OpenCV Preprocessor → Vision LLM → JSON Validator → Result

For Aadhaar/PAN with front+back in one image:
  → Preprocessor splits into 2 images
  → Each image processed separately
  → Results merged by FieldMerger

Engine selection (via VISION_ENGINE in .env):
  claude  → Claude Haiku 4.5 (testing, uses $5 credit)
  gemini  → Gemini 2.5 Flash (production)
"""

from __future__ import annotations

import asyncio
import re
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional

from backend.app.core.config import get_settings
from backend.app.core.logging import get_logger
from backend.app.schemas.documents import DocumentType, UploadedFile
from backend.app.schemas.extraction import (
    AadhaarExtraction,
    DocumentExtraction,
    DocumentStatus,
    ExtractionField,
    ExtractionResult,
    PANExtraction,
    ProcessingResponse,
)
from backend.app.services.ocr.document_preprocessor import DocumentPreprocessor
from backend.app.services.validation.field_validator import validate_all
from backend.app.services.ocr.vision_engine import (
    VisionResult,
    get_vision_engine,
)
from backend.app.utils.file_validator import cleanup_temp_file

logger = get_logger(__name__)

_THREAD_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="smartfill-vision")

# Minimum confidence for a field to be trusted without review flag
FIELD_CONFIDENCE_THRESHOLD = 65.0


class DocumentPipeline:
    """
    Vision LLM pipeline — processes uploaded documents end to end.
    One instance per app, reused across requests.
    """

    def __init__(self) -> None:
        self._preprocessor = DocumentPreprocessor()
        self._vision = get_vision_engine()
        settings = get_settings()

        logger.info(
            "Pipeline ready | engine=%s | vision_available=%s",
            settings.vision_engine,
            self._vision.is_available(),
        )

    async def process(self, files: list[UploadedFile]) -> ProcessingResponse:
        """Process all uploaded files concurrently."""
        start_ms = time.monotonic() * 1000

        if not files:
            return ProcessingResponse(
                status="error",
                has_errors=True,
                errors=["No documents provided."],
            )

        tasks = [self._process_one_async(f) for f in files]
        results: list[ExtractionResult] = await asyncio.gather(*tasks)

        elapsed_ms = (time.monotonic() * 1000) - start_ms
        logger.info(
            "Pipeline complete | docs=%d | time=%.0fms | statuses=%s",
            len(results), elapsed_ms, [r.status for r in results],
        )
        return ProcessingResponse.from_results(results, elapsed_ms)

    # ── Async wrapper ─────────────────────────────────────────────────────────

    async def _process_one_async(self, f: UploadedFile) -> ExtractionResult:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            _THREAD_POOL, self._process_one_sync, f
        )

    # ── Main sync pipeline per document ───────────────────────────────────────

    def _process_one_sync(self, uploaded: UploadedFile) -> ExtractionResult:
        doc_type = uploaded.document_type
        tmp_path = uploaded.tmp_path

        logger.info(
            "Processing | type=%s | file=%s",
            doc_type.value, uploaded.original_filename,
        )

        try:
            result = self._run_vision_pipeline(tmp_path, doc_type)
        except Exception as exc:
            logger.error("Pipeline crashed | type=%s | %s", doc_type.value, exc)
            result = ExtractionResult(
                document_type=doc_type.value,
                status=DocumentStatus.FAILED,
                error=f"Processing error: {type(exc).__name__}: {exc}",
            )
        finally:
            cleanup_temp_file(tmp_path)

        return result

    def _run_vision_pipeline(
        self, path: Path, doc_type: DocumentType
    ) -> ExtractionResult:
        """
        Full pipeline for one document:
          1. OpenCV preprocess (split, deskew, crop, resize)
          2. Vision LLM extract each preprocessed image
          3. Merge results if multiple images (front+back)
          4. Validate and normalise fields
          5. Populate typed schema (Aadhaar/PAN) for UI compatibility
        """

        # ── Step 1: Preprocess ────────────────────────────────────────────────
        try:
            prep_result = self._preprocessor.process(path)
        except Exception as exc:
            return ExtractionResult(
                document_type=doc_type.value,
                status=DocumentStatus.FAILED,
                error=f"Image preprocessing failed: {exc}",
            )

        if not prep_result.images:
            return ExtractionResult(
                document_type=doc_type.value,
                status=DocumentStatus.FAILED,
                error="No processable images found in file.",
            )

        logger.info(
            "Preprocessed | images=%d | split=%s | stages=%s",
            len(prep_result.images),
            prep_result.was_split,
            [img.stages for img in prep_result.images],
        )

        # ── Step 2: Vision LLM on each image ─────────────────────────────────
        vision_results: list[VisionResult] = []

        # For AUTO documents, classify first using quick OCR then use specific prompt
        effective_type = doc_type.value
        if doc_type == DocumentType.AUTO:
            effective_type = self._quick_classify(prep_result.images[0].jpeg_bytes)
            logger.info("Auto-classified | detected=%s", effective_type)

        for i, prep_img in enumerate(prep_result.images):
            label = (
                f"front" if prep_img.split_index == 1
                else "back" if prep_img.split_index == 2
                else "full"
            )
            logger.info("Vision LLM | image=%s | size=%s | type=%s", label, prep_img.final_size, effective_type)

            vr = self._vision.extract(
                jpeg_bytes=prep_img.jpeg_bytes,
                document_type=effective_type,
            )

            if vr.error:
                logger.warning("Vision failed | image=%s | %s", label, vr.error)
            else:
                logger.info(
                    "Vision OK | image=%s | fields=%d | tokens=%d+%d | time=%.0fms",
                    label, len(vr.fields),
                    vr.input_tokens, vr.output_tokens, vr.latency_ms,
                )

            vision_results.append(vr)

        # ── Step 3: Merge multi-image results ─────────────────────────────────
        merged_fields = self._merge_vision_results(vision_results)

        if not merged_fields:
            return ExtractionResult(
                document_type=doc_type.value,
                status=DocumentStatus.FAILED,
                error="Vision LLM returned no fields. Check image quality.",
            )

        # ── Step 4: Validate and normalise ────────────────────────────────────
        merged_fields = self._validate_fields(merged_fields, doc_type)

        # ── Step 5: Build ExtractionResult ────────────────────────────────────
        avg_conf = self._avg_confidence(merged_fields)
        engine_str = f"{vision_results[0].engine}:{vision_results[0].model}"

        # Build universal DocumentExtraction
        extraction = DocumentExtraction(
            document_type=effective_type,
            fields=merged_fields,
            ocr_engine=engine_str,
            avg_confidence=avg_conf,
            extraction_method="vision_llm",
        )

        has_values = any(f.value for f in merged_fields.values())
        status = DocumentStatus.SUCCESS if has_values else DocumentStatus.PARTIAL

        result = ExtractionResult(
            document_type=effective_type,
            status=status,
            extraction=extraction,
            ocr_engine_used=engine_str,
            avg_confidence=avg_conf,
            preprocessing_applied=[
                s for img in prep_result.images for s in img.stages
            ],
            extraction_method="vision_llm",
        )

        # Populate Milestone 1 typed schemas for UI compatibility
        if doc_type == DocumentType.AADHAAR:
            result.aadhaar = self._to_aadhaar(merged_fields)
        elif doc_type == DocumentType.PAN:
            result.pan = self._to_pan(merged_fields)

        return result

    # ── Merge multiple vision results (front+back) ────────────────────────────

    def _merge_vision_results(
        self, results: list[VisionResult]
    ) -> dict[str, ExtractionField]:
        """
        Merge fields from multiple images (e.g. Aadhaar front + back).
        Higher confidence wins per field.
        """
        merged: dict[str, ExtractionField] = {}

        for vr in results:
            if vr.error:
                continue
            for field_name, field_val in vr.fields.items():
                if not field_val.value:
                    continue
                existing = merged.get(field_name)
                if existing is None or field_val.confidence > existing.confidence:
                    merged[field_name] = field_val

        return merged

    # ── Field validation ──────────────────────────────────────────────────────

    def _validate_fields(
        self,
        fields: dict[str, ExtractionField],
        doc_type: DocumentType,
    ) -> dict[str, ExtractionField]:
        """
        Validate extracted values using format rules.
        Penalises confidence if format check fails.
        """
        validated = {}
        for field_name, ef in fields.items():
            if ef.value:
                ef = self._validate_one(field_name, ef, doc_type)
            validated[field_name] = ef
        return validated

    def _validate_one(
        self,
        name: str,
        ef: ExtractionField,
        doc_type: DocumentType,
    ) -> ExtractionField:
        value = ef.value
        confidence = ef.confidence

        # PAN number: must match AAAAA0000A
        if name == "pan_number":
            clean = re.sub(r"\s+", "", value.upper())
            if re.match(r"^[A-Z]{5}[0-9]{4}[A-Z]$", clean):
                value = clean
                confidence = min(confidence + 5, 100)
            else:
                confidence = max(confidence - 20, 0)

        # Aadhaar: must be 12 digits
        elif name == "aadhaar_number":
            clean = re.sub(r"[\s\-]", "", value)
            if re.match(r"^\d{12}$", clean):
                value = clean
                confidence = min(confidence + 5, 100)
            else:
                confidence = max(confidence - 20, 0)

        # DOB: must be YYYY-MM-DD
        elif name == "dob":
            if re.match(r"^\d{4}-\d{2}-\d{2}$", value):
                year = int(value[:4])
                if not (1900 <= year <= 2015):
                    confidence = max(confidence - 30, 0)
            else:
                confidence = max(confidence - 15, 0)

        # IFSC: AAAA0XXXXXX
        elif name == "ifsc":
            if re.match(r"^[A-Z]{4}0[A-Z0-9]{6}$", value.upper()):
                value = value.upper()
                confidence = min(confidence + 5, 100)
            else:
                confidence = max(confidence - 10, 0)

        # Mobile: 10 digits
        elif name == "mobile":
            clean = re.sub(r"\D", "", value)
            if len(clean) == 10:
                value = clean
            elif len(clean) == 12 and clean.startswith("91"):
                value = clean[2:]

        return ExtractionField.from_value(value, confidence)

    # ── Typed schema converters ───────────────────────────────────────────────

    def _to_aadhaar(
        self, fields: dict[str, ExtractionField]
    ) -> AadhaarExtraction:
        def get(k):
            return fields.get(k, ExtractionField.empty())
        return AadhaarExtraction(
            name=get("name"),
            father_name=get("father_name"),
            gender=get("gender"),
            dob=get("dob"),
            year_of_birth=get("year_of_birth"),
            aadhaar_number=get("aadhaar_number"),
        )

    def _to_pan(
        self, fields: dict[str, ExtractionField]
    ) -> PANExtraction:
        def get(k):
            return fields.get(k, ExtractionField.empty())
        return PANExtraction(
            name=get("name"),
            father_name=get("father_name"),
            dob=get("dob"),
            pan_number=get("pan_number"),
        )

    def _quick_classify(self, jpeg_bytes: bytes) -> str:
        """
        Stage 1: Classify document type using Vision LLM + Tesseract cross-check.

        Strategy:
          1. Run Vision LLM classify (most accurate for photos/cards)
          2. Run Tesseract keyword classify (most accurate for text-heavy PDFs)
          3. If both agree → confident, use it
          4. If they disagree → prefer Tesseract when its confidence is high (>=75%)
             because text-heavy documents (marks memos) are better read by OCR
          5. Otherwise → use Vision LLM result
        """
        vision_result = "unknown"
        tesseract_result = "unknown"
        tesseract_conf = 0.0

        # Stage 1a: Vision LLM classify
        try:
            vision_result = self._vision.classify(jpeg_bytes)
            logger.info("Vision classify | result=%s", vision_result)
        except Exception as exc:
            logger.warning("Vision classify failed | %s", exc)

        # Stage 1b: Tesseract keyword classify (especially good for text PDFs)
        try:
            import pytesseract, cv2, numpy as np, io
            from PIL import Image
            from backend.app.services.ai.classifier import DocumentClassifier
            img = Image.open(io.BytesIO(jpeg_bytes)).convert("RGB")
            grey = cv2.cvtColor(np.array(img), cv2.COLOR_RGB2GRAY)
            text = pytesseract.image_to_string(grey, config="--oem 3 --psm 6")
            if text.strip():
                dt, tesseract_conf = DocumentClassifier().classify(text)
                tesseract_result = dt.value
                logger.info("Tesseract classify | result=%s | conf=%.0f%%",
                            tesseract_result, tesseract_conf * 100)
        except Exception as exc:
            logger.warning("Tesseract classify failed | %s", exc)

        # Decision logic
        if vision_result == tesseract_result and vision_result != "unknown":
            # Both agree — highest confidence
            return vision_result

        if tesseract_result != "unknown" and tesseract_conf >= 0.75:
            # Tesseract is very confident — trust it (text-heavy documents)
            # This handles marks memos, certificates where OCR reads clearly
            logger.info("Using Tesseract result (high conf) | %s", tesseract_result)
            return tesseract_result

        if vision_result != "unknown":
            # Vision LLM result (handles photos, coloured cards)
            return vision_result

        if tesseract_result != "unknown" and tesseract_conf > 0.4:
            return tesseract_result

        return "unknown"

    def _avg_confidence(
        self, fields: dict[str, ExtractionField]
    ) -> float:
        confs = [f.confidence for f in fields.values() if f.value]
        return round(sum(confs) / len(confs), 1) if confs else 0.0
