"""
services/ocr/surya_engine.py
-----------------------------
Surya OCR engine — Tier 2 in the 3-tier OCR pipeline.

Surya is an open-source OCR model trained specifically on:
  - Indian language scripts (Devanagari, Telugu, Tamil, Kannada, etc.)
  - Mixed-script documents (Hindi+English on the same card)
  - Low-resolution and camera-captured documents

Why Surya over EasyOCR:
  - Specifically trained on Indian scripts
  - Better at mixed Hindi/English (Aadhaar cards)
  - Faster than EasyOCR on CPU
  - No GPU required (runs on cheap VPS)

When it's called:
  - Tesseract confidence < OCR_TIER2_CONFIDENCE threshold
  - Or document type known to need Indian script support

Models are downloaded once (~500MB) and cached in ~/.cache/surya.
First call takes 10-30 seconds for model loading.
Subsequent calls are fast (2-5 seconds on CPU).
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Optional

import numpy as np
from PIL import Image

from backend.app.core.config import get_settings
from backend.app.core.exceptions import OCRError
from backend.app.core.logging import get_logger
from backend.app.services.ocr.engine import OCRResult
from backend.app.services.ocr.pdf_converter import pdf_to_images
from backend.app.services.ocr.preprocessor import ImagePreprocessor

logger = get_logger(__name__)

# Module-level model instances (lazy-loaded, cached)
_recognition_predictor = None
_detection_predictor = None
_surya_load_failed = False   # cached failure flag — don't retry after first fail


def _get_predictors():
    """
    Lazy-load Surya models on first use.
    Models are cached in module-level variables so they're
    loaded once per process, not once per request.

    If loading fails (e.g. network unavailable, model server down),
    sets _surya_load_failed=True so subsequent requests skip Surya
    immediately instead of waiting for 3 download retries each time.
    """
    global _recognition_predictor, _detection_predictor, _surya_load_failed

    if _surya_load_failed:
        raise OCRError("Surya models unavailable (previous load failed)")

    if _recognition_predictor is None:
        logger.info("Loading Surya OCR models (first-time, may take 30s)...")
        start = time.monotonic()
        try:
            from surya.recognition import RecognitionPredictor
            from surya.detection import DetectionPredictor
            _detection_predictor   = DetectionPredictor()
            _recognition_predictor = RecognitionPredictor()
            elapsed = time.monotonic() - start
            logger.info("Surya models loaded | time=%.1fs", elapsed)
        except Exception as exc:
            _surya_load_failed = True
            logger.error(
                "Failed to load Surya models — will use Tesseract only | error=%s", exc
            )
            raise OCRError(f"Surya model loading failed: {exc}") from exc

    return _recognition_predictor, _detection_predictor


class SuryaOCREngine:
    """
    Stateless Surya OCR engine.

    Usage:
        engine = SuryaOCREngine()
        result = engine.extract(file_path)
    """

    def __init__(self) -> None:
        self._settings = get_settings()
        self._preprocessor = ImagePreprocessor()

    def is_available(self) -> bool:
        """Check if Surya is importable (may not be installed)."""
        try:
            import surya  # noqa: F401
            return True
        except ImportError:
            return False

    def extract(self, file_path: Path) -> OCRResult:
        """
        Extract text from an image or PDF using Surya OCR.

        Args:
            file_path: Path to PDF, PNG, JPG, or JPEG.

        Returns:
            OCRResult with extracted text and confidence scores.

        Raises:
            OCRError: If Surya fails or returns empty text.
        """
        logger.info("Surya OCR started | file=%s", file_path.name)
        start_ms = time.monotonic() * 1000

        extension = file_path.suffix.lstrip(".").lower()

        if extension == "pdf":
            images = self._pdf_to_pil_images(file_path)
        else:
            images = [self._load_and_preprocess(file_path)]

        all_texts: list[str] = []
        all_confidences: list[float] = []

        rec_pred, det_pred = _get_predictors()

        for page_num, pil_image in enumerate(images):
            logger.info("Surya OCR page %d/%d", page_num + 1, len(images))
            try:
                text, confidence = self._ocr_single_image(
                    pil_image, rec_pred, det_pred
                )
                all_texts.append(text)
                all_confidences.append(confidence)
            except Exception as exc:
                logger.warning(
                    "Surya failed on page %d | error=%s", page_num + 1, exc
                )
                all_texts.append("")
                all_confidences.append(0.0)

        combined_text = "\n\n".join(t for t in all_texts if t.strip())
        avg_conf = (
            sum(all_confidences) / len(all_confidences)
            if all_confidences else 0.0
        )

        elapsed_ms = (time.monotonic() * 1000) - start_ms
        logger.info(
            "Surya OCR complete | conf=%.1f%% | words=%d | time=%.0fms",
            avg_conf,
            len(combined_text.split()),
            elapsed_ms,
        )

        if not combined_text.strip():
            raise OCRError(
                "Surya OCR returned empty text",
                details={"file": file_path.name},
            )

        return OCRResult(
            text=combined_text,
            avg_confidence=round(avg_conf, 1),
            word_count=len(combined_text.split()),
            engine="surya",
            preprocessing=["surya_ocr"],
        )

    # ── Private helpers ───────────────────────────────────────────────────────

    def _ocr_single_image(
        self,
        pil_image: Image.Image,
        rec_pred,
        det_pred,
    ) -> tuple[str, float]:
        """
        Run Surya detection + recognition on a single PIL image.

        Returns:
            (text, avg_confidence) tuple.
        """
        # Surya works on PIL Images directly
        # Detection: find text regions
        # Recognition: read text in each region
        predictions = rec_pred(
            [pil_image],
            det_pred,
            langs=[["en", "hi", "te", "ta", "kn", "ml"]],
        )

        if not predictions:
            return "", 0.0

        page_pred = predictions[0]
        lines: list[str] = []
        confidences: list[float] = []

        for line in page_pred.text_lines:
            text = line.text.strip()
            if text:
                lines.append(text)
                confidences.append(line.confidence * 100)

        combined = "\n".join(lines)
        avg_conf = sum(confidences) / len(confidences) if confidences else 0.0

        return combined, avg_conf

    def _load_and_preprocess(self, image_path: Path) -> Image.Image:
        """Load and preprocess image, return as PIL Image for Surya."""
        try:
            # Run our preprocessor first to fix brightness, noise etc.
            prep = self._preprocessor.process(image_path)
            # Convert numpy array back to PIL (Surya needs PIL)
            if len(prep.image.shape) == 2:
                # Greyscale binary — convert to RGB for Surya
                pil = Image.fromarray(prep.image, mode='L').convert('RGB')
            else:
                pil = Image.fromarray(prep.image)
            return pil
        except Exception:
            # Fallback: load original without preprocessing
            return Image.open(image_path).convert('RGB')

    def _pdf_to_pil_images(self, pdf_path: Path) -> list[Image.Image]:
        """Convert PDF to list of PIL Images for Surya."""
        try:
            np_images = pdf_to_images(pdf_path)
            return [Image.fromarray(arr) for arr in np_images]
        except Exception as exc:
            raise OCRError(
                f"PDF conversion failed for Surya: {exc}",
                details={"file": pdf_path.name},
            ) from exc
