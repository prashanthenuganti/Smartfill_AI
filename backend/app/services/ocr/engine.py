"""
services/ocr/engine.py
-----------------------
Tesseract OCR engine with confidence scoring and retry logic.

Design decisions:
  1. Returns BOTH the extracted text AND per-word confidence scores.
     The average confidence drives the "needs_review" flag on each field.

  2. Two-pass retry strategy:
     Pass 1 → standard preprocessing → Tesseract
     Pass 2 (if confidence < threshold) → aggressive preprocessing → Tesseract
     Aggressive pass uses stronger denoising and higher-contrast binarisation.

  3. Language config: eng+hin
     Most Indian documents have English text. Hindi is added so Tesseract
     doesn't treat Devanagari characters as noise.

  4. Page Segmentation Mode (PSM):
     PSM 6 → "Assume a single uniform block of text" — best for ID cards
     which have structured label/value layouts but no multi-column flow.

  5. OEM 3 → "LSTM + Legacy" engine (best accuracy on Tesseract 5.x).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import cv2
import numpy as np
import pytesseract

from backend.app.core.config import get_settings
from backend.app.core.exceptions import LowConfidenceError, OCRError
from backend.app.core.logging import get_logger
from backend.app.services.ocr.pdf_converter import pdf_to_images
from backend.app.services.ocr.preprocessor import ImagePreprocessor, PreprocessResult

logger = get_logger(__name__)

# ── Tesseract configuration ───────────────────────────────────────────────────

_TESSERACT_CONFIG = "--oem 3 --psm 6"
_TESSERACT_LANG = "eng"         # start with English; add +hin if Hindi pack installed

# Aggressive preprocessing constants (retry pass)
_AGGRESSIVE_DENOISE_H = 20
_AGGRESSIVE_BLOCK_SIZE = 21     # smaller adaptive threshold block
_AGGRESSIVE_C = 8


@dataclass
class OCRResult:
    """
    Result from a single OCR pass on one image.

    text:            Full extracted text (newline-separated lines).
    avg_confidence:  Mean Tesseract word confidence (0–100).
    word_count:      Number of words extracted.
    engine:          Which engine/pass produced this result ("tesseract_pass1/2").
    preprocessing:   Stages applied before OCR.
    """
    text: str
    avg_confidence: float
    word_count: int
    engine: str
    preprocessing: list[str]


class TesseractOCREngine:
    """
    Stateless OCR engine.

    Usage:
        engine = TesseractOCREngine()
        result = engine.extract(file_path)
        print(result.text)
    """

    def __init__(self) -> None:
        settings = get_settings()
        pytesseract.pytesseract.tesseract_cmd = settings.tesseract_cmd
        self._threshold = settings.ocr_confidence_threshold
        self._preprocessor = ImagePreprocessor()

    def extract(self, file_path: Path) -> OCRResult:
        """
        Extract text from an image or PDF file.

        For PDFs: converts all pages to images, runs OCR on each page,
        and concatenates results.

        Args:
            file_path: Path to PDF, PNG, JPG, or JPEG file.

        Returns:
            OCRResult with combined text and average confidence.

        Raises:
            OCRError:          Tesseract failed or returned empty text.
            LowConfidenceError: Both passes produced low-confidence results.
        """
        extension = file_path.suffix.lstrip(".").lower()

        if extension == "pdf":
            return self._extract_pdf(file_path)
        else:
            return self._extract_image(file_path)

    # ── PDF handling ──────────────────────────────────────────────────────────

    def _extract_pdf(self, pdf_path: Path) -> OCRResult:
        """Convert PDF → images, OCR each page, merge results."""
        logger.info("OCR started (PDF) | file=%s", pdf_path.name)

        try:
            page_images = pdf_to_images(pdf_path)
        except ValueError as exc:
            raise OCRError(str(exc)) from exc

        all_texts: list[str] = []
        all_confidences: list[float] = []
        all_preprocessing: list[str] = []
        total_words = 0

        for page_num, page_img in enumerate(page_images):
            logger.info("OCR page %d/%d", page_num + 1, len(page_images))
            result = self._ocr_numpy_image(page_img, page_label=f"pdf_p{page_num+1}")
            all_texts.append(result.text)
            all_confidences.append(result.avg_confidence)
            all_preprocessing.extend(result.preprocessing)
            total_words += result.word_count

        combined_text = "\n\n".join(t for t in all_texts if t.strip())
        avg_conf = sum(all_confidences) / len(all_confidences) if all_confidences else 0.0

        return OCRResult(
            text=combined_text,
            avg_confidence=round(avg_conf, 1),
            word_count=total_words,
            engine="tesseract_pdf",
            preprocessing=all_preprocessing,
        )

    # ── Image handling ────────────────────────────────────────────────────────

    def _extract_image(self, image_path: Path) -> OCRResult:
        """Preprocess and OCR a single image file."""
        logger.info("OCR started (image) | file=%s", image_path.name)

        prep_result = self._preprocessor.process(image_path)
        return self._ocr_numpy_image(
            prep_result.image,
            page_label="img",
            preprocessing_done=prep_result.stages_applied,
        )

    def _ocr_numpy_image(
        self,
        img: np.ndarray,
        page_label: str = "img",
        preprocessing_done: Optional[list[str]] = None,
    ) -> OCRResult:
        """
        Run OCR on a preprocessed numpy image with two-pass retry.

        Pass 1: standard preprocessing.
        Pass 2: aggressive preprocessing if pass 1 confidence < threshold.
        """
        # If img came from PDF it may not have been preprocessed yet
        if preprocessing_done is None:
            prep = self._preprocessor.process_from_array(img)
            processed_img = prep.image
            preprocessing_done = prep.stages_applied
        else:
            processed_img = img

        # ── Pass 1 ────────────────────────────────────────────────────────────
        result = self._run_tesseract(processed_img, engine_label="tesseract_pass1")
        result.preprocessing = preprocessing_done

        logger.info(
            "OCR pass 1 | label=%s | words=%d | avg_confidence=%.1f",
            page_label, result.word_count, result.avg_confidence,
        )

        if result.avg_confidence >= self._threshold and result.word_count > 0:
            return result

        # ── Pass 2 (aggressive preprocessing) ────────────────────────────────
        logger.info(
            "OCR pass 1 below threshold (%.1f < %d) — retrying with aggressive preprocessing",
            result.avg_confidence, self._threshold,
        )

        aggressive_img = self._aggressive_preprocess(processed_img)
        result2 = self._run_tesseract(aggressive_img, engine_label="tesseract_pass2")
        result2.preprocessing = preprocessing_done + ["aggressive_retry"]

        logger.info(
            "OCR pass 2 | label=%s | words=%d | avg_confidence=%.1f",
            page_label, result2.word_count, result2.avg_confidence,
        )

        # Return whichever pass produced higher confidence
        best = result2 if result2.avg_confidence > result.avg_confidence else result

        if best.word_count == 0:
            raise OCRError(
                f"Tesseract returned empty text after both passes for '{page_label}'.",
                details={"pass1_confidence": result.avg_confidence,
                         "pass2_confidence": result2.avg_confidence},
            )

        return best

    def _run_tesseract(self, img: np.ndarray, engine_label: str) -> OCRResult:
        """
        Call Tesseract on a preprocessed numpy image.

        Uses image_to_data (TSV output) to get per-word confidence scores,
        then reconstructs the full text from confident words.
        """
        try:
            data = pytesseract.image_to_data(
                img,
                lang=_TESSERACT_LANG,
                config=_TESSERACT_CONFIG,
                output_type=pytesseract.Output.DICT,
            )
        except pytesseract.TesseractError as exc:
            raise OCRError(
                f"Tesseract engine error: {exc}",
                details={"engine": engine_label},
            ) from exc

        # Extract confidence scores and words (conf == -1 means non-word token)
        confidences = [
            int(c) for c in data["conf"] if int(c) >= 0
        ]
        words = [
            w for w, c in zip(data["text"], data["conf"])
            if int(c) >= 0 and w.strip()
        ]

        avg_conf = sum(confidences) / len(confidences) if confidences else 0.0

        # Reconstruct text preserving line structure from block/line numbers
        text = self._reconstruct_text(data)

        return OCRResult(
            text=text,
            avg_confidence=round(avg_conf, 1),
            word_count=len(words),
            engine=engine_label,
            preprocessing=[],
        )

    def _reconstruct_text(self, data: dict) -> str:
        """
        Reconstruct multi-line text from Tesseract TSV output.

        Tesseract's image_to_data returns individual words with block/line
        numbers. We group by (block_num, line_num) to reassemble lines,
        preserving the original layout as much as possible.
        """
        lines: dict[tuple[int, int], list[str]] = {}

        for i, word in enumerate(data["text"]):
            if not word.strip():
                continue
            conf = int(data["conf"][i])
            if conf < 0:
                continue
            key = (data["block_num"][i], data["line_num"][i])
            lines.setdefault(key, []).append(word)

        # Sort by block then line, join words within each line
        sorted_keys = sorted(lines.keys())
        return "\n".join(" ".join(lines[k]) for k in sorted_keys)

    def _aggressive_preprocess(self, img: np.ndarray) -> np.ndarray:
        """
        Stronger preprocessing for retry pass on low-confidence images.

        Applied on top of the already-preprocessed image from pass 1.
        """
        # Stronger denoising
        if len(img.shape) == 2:
            denoised = cv2.fastNlMeansDenoising(img, h=_AGGRESSIVE_DENOISE_H)
        else:
            denoised = img

        # Re-binarise with tighter parameters
        binary = cv2.adaptiveThreshold(
            denoised,
            maxValue=255,
            adaptiveMethod=cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            thresholdType=cv2.THRESH_BINARY,
            blockSize=_AGGRESSIVE_BLOCK_SIZE,
            C=_AGGRESSIVE_C,
        )

        # Dilate slightly to thicken thin strokes (helps with faded PAN cards)
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
        dilated = cv2.dilate(binary, kernel, iterations=1)

        return dilated
