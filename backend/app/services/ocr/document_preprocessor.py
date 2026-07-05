"""
services/ocr/document_preprocessor.py
----------------------------------------
Production OpenCV preprocessing pipeline for Indian government documents.

Pipeline per image:
  1. Load + validate
  2. Detect if image contains front+back of card (split and process each)
  3. Auto-deskew (correct camera tilt)
  4. Auto-crop to document boundary (remove background)
  5. Resize longest edge to 1000px
  6. Save as JPEG quality 85

Why each step matters:
  Split detection  → prevents address from bleeding into name field
  Deskew          → straightens tilted phone photos before Gemini reads them
  Crop            → removes background noise, reduces image tokens by 30-40%
  Resize to 1000px → ~600 image tokens vs ~3000 at full resolution = 60% cost saving
  JPEG 85         → 60% smaller file, no visible text quality loss

Output: list of preprocessed PIL Images ready for Gemini API
"""

from __future__ import annotations

import io
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import cv2
import numpy as np
from PIL import Image

from backend.app.core.logging import get_logger
from backend.app.services.ocr.pdf_converter import pdf_to_images

logger = get_logger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

TARGET_LONG_EDGE   = 1000          # px — Gemini sweet spot for documents
JPEG_QUALITY       = 85            # compress without losing text
MIN_CARD_HEIGHT    = 150           # px — ignore tiny noise crops
GAP_BRIGHTNESS     = 215           # row mean above this = gap between cards
MIN_GAP_ROWS       = 30            # rows needed to confirm a real gap
MAX_DESKEW_ANGLE   = 12.0          # degrees — cap to prevent catastrophic rotation
CROP_PADDING       = 12            # px — padding around detected document boundary


@dataclass
class PreprocessedImage:
    """One processed card/page ready for Gemini."""
    pil_image: Image.Image
    jpeg_bytes: bytes
    original_size: tuple[int, int]
    final_size: tuple[int, int]
    stages: list[str] = field(default_factory=list)
    is_split_part: bool = False
    split_index: int = 0           # 0=full, 1=front, 2=back


@dataclass
class PreprocessResult:
    """Result of preprocessing one uploaded file."""
    images: list[PreprocessedImage]
    original_filename: str
    processing_time_ms: float = 0.0
    was_split: bool = False
    was_pdf: bool = False


class DocumentPreprocessor:
    """
    Production OpenCV preprocessing pipeline.

    Usage:
        preprocessor = DocumentPreprocessor()
        result = preprocessor.process(Path("aadhaar.jpg"))
        # result.images is a list of PreprocessedImage
        # usually 1 image, or 2 if front+back detected
    """

    def process(self, file_path: Path) -> PreprocessResult:
        start = time.monotonic()
        logger.info("Preprocessing | file=%s", file_path.name)

        is_pdf = file_path.suffix.lower() == ".pdf"
        raw_images: list[np.ndarray] = []

        if is_pdf:
            pages = pdf_to_images(file_path)
            raw_images = pages
            logger.info("PDF converted | pages=%d", len(pages))
        else:
            arr = self._load_image(file_path)
            if arr is None:
                raise ValueError(f"Cannot load image: {file_path.name}")
            raw_images = [arr]

        # Process each page/image
        all_processed: list[PreprocessedImage] = []
        was_split = False

        for page_idx, arr in enumerate(raw_images):
            # Detect and split front+back if needed
            splits = self._detect_and_split(arr)
            if len(splits) > 1:
                was_split = True
                logger.info("Front+back split detected | parts=%d", len(splits))

            for split_idx, sub_arr in enumerate(splits):
                processed = self._process_single(
                    sub_arr,
                    is_split_part=len(splits) > 1,
                    split_index=split_idx + 1 if len(splits) > 1 else 0,
                )
                all_processed.append(processed)

        elapsed = (time.monotonic() - start) * 1000
        logger.info(
            "Preprocessing done | images=%d | split=%s | time=%.0fms",
            len(all_processed), was_split, elapsed,
        )

        return PreprocessResult(
            images=all_processed,
            original_filename=file_path.name,
            processing_time_ms=round(elapsed, 1),
            was_split=was_split,
            was_pdf=is_pdf,
        )

    # ── Split detection ───────────────────────────────────────────────────────

    def _detect_and_split(self, arr: np.ndarray) -> list[np.ndarray]:
        """
        Detect if image contains front+back of a card stacked vertically.

        Method: find a bright horizontal band in the middle third of the image.
        A white gap separating two cards has row-mean brightness > GAP_BRIGHTNESS
        and spans at least MIN_GAP_ROWS consecutive rows.
        """
        h, w = arr.shape[:2]

        # Only check for vertical splits (image taller than wide)
        # Horizontal splits (side by side) handled separately
        if h < w * 1.2:
            # Check side-by-side (landscape orientation)
            return self._check_horizontal_split(arr)

        grey = cv2.cvtColor(arr, cv2.COLOR_BGR2GRAY) if len(arr.shape) == 3 else arr
        row_means = np.mean(grey, axis=1)

        # Look for gap in middle half of image
        search_start = h // 4
        search_end = 3 * h // 4
        search_means = row_means[search_start:search_end]

        # Find consecutive bright rows
        bright_mask = search_means > GAP_BRIGHTNESS
        gap_center = self._find_gap_center(bright_mask, search_start)

        if gap_center is None:
            return [arr]

        # Split at gap center
        top = arr[:gap_center, :]
        bottom = arr[gap_center:, :]

        # Only split if both halves are large enough to be real cards
        if top.shape[0] < MIN_CARD_HEIGHT or bottom.shape[0] < MIN_CARD_HEIGHT:
            return [arr]

        logger.info("Split at y=%d (%.0f%% from top)", gap_center, gap_center/h*100)
        return [top, bottom]

    def _check_horizontal_split(self, arr: np.ndarray) -> list[np.ndarray]:
        """Check for side-by-side layout (front and back placed next to each other)."""
        h, w = arr.shape[:2]
        if w < h * 1.5:
            return [arr]

        grey = cv2.cvtColor(arr, cv2.COLOR_BGR2GRAY) if len(arr.shape) == 3 else arr
        col_means = np.mean(grey, axis=0)

        search_start = w // 3
        search_end = 2 * w // 3
        bright_mask = col_means[search_start:search_end] > GAP_BRIGHTNESS
        gap_center = self._find_gap_center(bright_mask, search_start)

        if gap_center is None:
            return [arr]

        left = arr[:, :gap_center]
        right = arr[:, gap_center:]

        if left.shape[1] < 100 or right.shape[1] < 100:
            return [arr]

        return [left, right]

    def _find_gap_center(
        self, bright_mask: np.ndarray, offset: int
    ) -> Optional[int]:
        """Find center of longest consecutive bright band."""
        best_start = best_end = best_len = 0
        cur_start = cur_len = 0

        for i, is_bright in enumerate(bright_mask):
            if is_bright:
                if cur_len == 0:
                    cur_start = i
                cur_len += 1
                if cur_len > best_len:
                    best_len = cur_len
                    best_start = cur_start
                    best_end = i
            else:
                cur_len = 0

        if best_len < MIN_GAP_ROWS:
            return None

        return offset + (best_start + best_end) // 2

    # ── Single image processing ───────────────────────────────────────────────

    def _process_single(
        self,
        arr: np.ndarray,
        is_split_part: bool = False,
        split_index: int = 0,
    ) -> PreprocessedImage:
        """Apply full preprocessing pipeline to one card image."""
        stages: list[str] = []
        orig_h, orig_w = arr.shape[:2]

        # 1. Convert to RGB (OpenCV loads BGR)
        if len(arr.shape) == 3:
            arr = cv2.cvtColor(arr, cv2.COLOR_BGR2RGB)

        # 2. Auto-deskew
        arr, deskew_angle = self._deskew(arr)
        if abs(deskew_angle) > 0.5:
            stages.append(f"deskew:{deskew_angle:.1f}°")

        # 3. Auto-crop to document boundary
        cropped = self._auto_crop(arr)
        if cropped is not None:
            arr = cropped
            stages.append("autocrop")

        # 4. Resize to target
        arr = self._resize(arr)
        stages.append(f"resize:{orig_w}×{orig_h}→{arr.shape[1]}×{arr.shape[0]}")

        # 5. Convert to PIL
        pil = Image.fromarray(arr)

        # 6. Encode as JPEG
        buf = io.BytesIO()
        pil.save(buf, format="JPEG", quality=JPEG_QUALITY, optimize=True)
        jpeg_bytes = buf.getvalue()

        logger.info(
            "Processed card | stages=%s | final=%dx%d | size=%.0fKB",
            stages, arr.shape[1], arr.shape[0], len(jpeg_bytes) / 1024,
        )

        return PreprocessedImage(
            pil_image=pil,
            jpeg_bytes=jpeg_bytes,
            original_size=(orig_w, orig_h),
            final_size=(arr.shape[1], arr.shape[0]),
            stages=stages,
            is_split_part=is_split_part,
            split_index=split_index,
        )

    # ── Deskew ────────────────────────────────────────────────────────────────

    def _deskew(self, arr: np.ndarray) -> tuple[np.ndarray, float]:
        """
        Correct camera tilt using Hough line detection.

        More reliable than minAreaRect for deskew because it looks for
        actual text lines rather than the document border.
        """
        grey = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
        edges = cv2.Canny(grey, 50, 150, apertureSize=3)
        lines = cv2.HoughLinesP(
            edges, 1, np.pi / 180,
            threshold=100, minLineLength=100, maxLineGap=10
        )

        if lines is None:
            return arr, 0.0

        # cv2.HoughLinesP's return shape varies by OpenCV build/version —
        # normally (N, 1, 4), but sometimes (N, 4) directly. reshape(-1, 4)
        # normalises either case so the unpack below never fails.
        lines = lines.reshape(-1, 4)

        angles = []
        for x1, y1, x2, y2 in lines:
            if x2 != x1:
                angle = np.degrees(np.arctan2(y2 - y1, x2 - x1))
                if abs(angle) < MAX_DESKEW_ANGLE:
                    angles.append(angle)

        if not angles:
            return arr, 0.0

        median_angle = float(np.median(angles))
        if abs(median_angle) < 0.5:
            return arr, 0.0

        h, w = arr.shape[:2]
        M = cv2.getRotationMatrix2D((w / 2, h / 2), median_angle, 1.0)
        rotated = cv2.warpAffine(
            arr, M, (w, h),
            flags=cv2.INTER_CUBIC,
            borderMode=cv2.BORDER_REPLICATE,
        )
        return rotated, median_angle

    # ── Auto-crop ─────────────────────────────────────────────────────────────

    def _auto_crop(self, arr: np.ndarray) -> Optional[np.ndarray]:
        """
        Detect document boundary and crop to it.

        Uses contour detection to find the largest rectangular region.
        Falls back to None (no crop) if detection is unreliable.
        """
        grey = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
        blurred = cv2.GaussianBlur(grey, (5, 5), 0)
        _, thresh = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

        # Find contours
        contours, _ = cv2.findContours(
            thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
        )
        if not contours:
            return None

        # Take the largest contour
        largest = max(contours, key=cv2.contourArea)
        img_area = arr.shape[0] * arr.shape[1]

        # Only crop if contour covers 20-95% of image (real document)
        contour_area = cv2.contourArea(largest)
        coverage = contour_area / img_area
        if not (0.20 <= coverage <= 0.95):
            return None

        x, y, cw, ch = cv2.boundingRect(largest)

        # Add padding
        x = max(0, x - CROP_PADDING)
        y = max(0, y - CROP_PADDING)
        cw = min(arr.shape[1] - x, cw + CROP_PADDING * 2)
        ch = min(arr.shape[0] - y, ch + CROP_PADDING * 2)

        # Don't crop if result is tiny
        if cw < 100 or ch < 100:
            return None

        return arr[y:y + ch, x:x + cw]

    # ── Resize ────────────────────────────────────────────────────────────────

    def _resize(self, arr: np.ndarray) -> np.ndarray:
        """Resize so longest edge = TARGET_LONG_EDGE. Upscale small images."""
        h, w = arr.shape[:2]
        long_edge = max(h, w)

        if long_edge == TARGET_LONG_EDGE:
            return arr

        scale = TARGET_LONG_EDGE / long_edge
        new_w = int(w * scale)
        new_h = int(h * scale)

        interp = cv2.INTER_AREA if scale < 1 else cv2.INTER_LANCZOS4
        return cv2.resize(arr, (new_w, new_h), interpolation=interp)

    # ── Load ──────────────────────────────────────────────────────────────────

    def _load_image(self, path: Path) -> Optional[np.ndarray]:
        try:
            arr = cv2.imread(str(path))
            if arr is None:
                # Try PIL as fallback (handles WebP, HEIC, etc.)
                pil = Image.open(path).convert("RGB")
                arr = cv2.cvtColor(np.array(pil), cv2.COLOR_RGB2BGR)
            return arr
        except Exception as exc:
            logger.error("Image load failed | file=%s | %s", path.name, exc)
            return None
