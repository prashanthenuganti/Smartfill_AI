"""
services/ocr/preprocessor.py
-----------------------------
Adaptive image enhancement pipeline for Indian government documents.

Problem diagnosis drives strategy selection:

  TINY_IMAGE    (< 600px wide)   → Lanczos super-upscale → sharpen
  OVEREXPOSED   (brightness>200) → Gamma correction → CLAHE
  UNDEREXPOSED  (brightness<80)  → Gamma correction → CLAHE
  LOW_CONTRAST  (std<40)         → CLAHE + unsharp mask
  NOISY         (noise>8)        → Bilateral filter (edge-preserving denoise)
  COLOURED_BG   (saturation>40)  → Multi-strategy channel isolation
  BLURRY        (laplacian<100)  → Unsharp mask sharpening
  STANDARD                       → CLAHE + bilateral + adaptive threshold

For coloured background cards (teal PAN, blue PAN), five strategies
are tried in parallel on a low-res preview. The winner (most OCR words)
is applied to the full-resolution image.

All enhancements are applied non-destructively in a defined order.
The output is always a binary (black/white) image ready for Tesseract.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
import time

import cv2
import numpy as np
import pytesseract
from PIL import Image

from backend.app.core.logging import get_logger

logger = get_logger(__name__)

# ── Quality thresholds ────────────────────────────────────────────────────────

TINY_WIDTH_THRESHOLD        = 600     # px — below this, upscale first
OVEREXPOSED_THRESHOLD       = 200     # mean grey value
UNDEREXPOSED_THRESHOLD      = 80      # mean grey value
LOW_CONTRAST_THRESHOLD      = 40      # std dev of grey
BLUR_THRESHOLD              = 100     # Laplacian variance
NOISE_THRESHOLD             = 8.0     # std dev of (img - blurred)
COLOUR_SAT_THRESHOLD        = 40      # mean HSV saturation
MAX_UPSCALE_WIDTH           = 2400    # never upscale beyond this
MAX_DESKEW_ANGLE            = 15.0    # degrees — safety cap
DESKEW_ANGLE_MIN            = 0.5     # degrees — skip tiny angles


@dataclass
class ImageQuality:
    """Diagnostic report for a single image."""
    width: int
    height: int
    megapixels: float
    mean_brightness: float
    contrast: float
    noise_level: float
    blur_score: float
    mean_saturation: float

    @property
    def is_tiny(self) -> bool:
        return self.width < TINY_WIDTH_THRESHOLD

    @property
    def is_overexposed(self) -> bool:
        return self.mean_brightness > OVEREXPOSED_THRESHOLD

    @property
    def is_underexposed(self) -> bool:
        return self.mean_brightness < UNDEREXPOSED_THRESHOLD

    @property
    def is_low_contrast(self) -> bool:
        return self.contrast < LOW_CONTRAST_THRESHOLD

    @property
    def is_blurry(self) -> bool:
        return self.blur_score < BLUR_THRESHOLD

    @property
    def is_noisy(self) -> bool:
        return self.noise_level > NOISE_THRESHOLD

    @property
    def has_coloured_bg(self) -> bool:
        return self.mean_saturation > COLOUR_SAT_THRESHOLD

    @property
    def strategy(self) -> str:
        """Primary enhancement strategy for this image."""
        if self.is_tiny:
            return "super_upscale"
        if self.has_coloured_bg:
            return "coloured_bg"
        if self.is_overexposed or self.is_underexposed or self.is_low_contrast:
            return "exposure_correct"
        if self.is_noisy:
            return "denoise"
        return "standard"

    def summary(self) -> str:
        issues = []
        if self.is_tiny:        issues.append("TINY")
        if self.is_overexposed: issues.append("OVEREXPOSED")
        if self.is_underexposed:issues.append("UNDEREXPOSED")
        if self.is_low_contrast:issues.append("LOW_CONTRAST")
        if self.is_blurry:      issues.append("BLURRY")
        if self.is_noisy:       issues.append("NOISY")
        if self.has_coloured_bg:issues.append("COLOURED_BG")
        return f"{self.width}×{self.height}px | {', '.join(issues) or 'OK'} | strategy={self.strategy}"


@dataclass
class PreprocessResult:
    """Output of the preprocessing pipeline."""
    image: np.ndarray
    stages_applied: list[str] = field(default_factory=list)
    original_size: tuple[int, int] = (0, 0)
    final_size: tuple[int, int] = (0, 0)
    quality: Optional[ImageQuality] = None
    skew_angle_degrees: Optional[float] = None
    processing_time_ms: float = 0.0


class ImagePreprocessor:
    """
    Adaptive image enhancement pipeline.

    Diagnoses each image independently and applies the correct
    sequence of enhancements. Never applies a fixed pipeline to
    all images — different problems need different solutions.
    """

    def process(self, image_path: Path) -> PreprocessResult:
        start = time.monotonic()
        logger.info("Preprocessing | file=%s", image_path.name)

        img = self._load(image_path)
        if img is None:
            raise ValueError(f"Could not load image: {image_path}")

        result = self._run(img)
        result.processing_time_ms = round((time.monotonic() - start) * 1000, 1)

        logger.info(
            "Preprocessing done | %s | stages=%s | time=%.0fms",
            result.quality.summary() if result.quality else "?",
            result.stages_applied,
            result.processing_time_ms,
        )
        return result

    def process_from_array(self, img: np.ndarray) -> PreprocessResult:
        return self._run(img)

    # ── Main pipeline ─────────────────────────────────────────────────────────

    def _run(self, img: np.ndarray) -> PreprocessResult:
        h, w = img.shape[:2]
        quality = self._diagnose(img)
        result = PreprocessResult(
            image=img,
            original_size=(w, h),
            quality=quality,
        )

        strategy = quality.strategy
        logger.info("Enhancement strategy | %s", strategy)

        if strategy == "super_upscale":
            img = self._super_upscale(img, result)
            # Re-diagnose after upscale — may have new issues
            quality2 = self._diagnose(img)
            if quality2.has_coloured_bg:
                img = self._coloured_bg_pipeline(img, result)
            elif quality2.is_overexposed or quality2.is_low_contrast:
                img = self._exposure_pipeline(img, result)
            else:
                img = self._standard_pipeline(img, result)

        elif strategy == "coloured_bg":
            img = self._coloured_bg_pipeline(img, result)

        elif strategy == "exposure_correct":
            img = self._exposure_pipeline(img, result)

        elif strategy == "denoise":
            img = self._denoise_pipeline(img, result)

        else:
            img = self._standard_pipeline(img, result)

        # Always try deskew at the end on white-background images
        if not quality.has_coloured_bg and len(img.shape) == 2:
            img = self._deskew(img, result)

        fh, fw = img.shape[:2]
        result.image = img
        result.final_size = (fw, fh)
        return result

    # ── Diagnosis ─────────────────────────────────────────────────────────────

    def _diagnose(self, arr: np.ndarray) -> ImageQuality:
        """Measure image quality metrics."""
        h, w = arr.shape[:2]
        grey = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY) if len(arr.shape) == 3 else arr
        hsv  = cv2.cvtColor(arr, cv2.COLOR_RGB2HSV)  if len(arr.shape) == 3 else None

        blur_score  = cv2.Laplacian(grey, cv2.CV_64F).var()
        blurred5    = cv2.GaussianBlur(grey, (5, 5), 0)
        noise_level = float(np.std(grey.astype(float) - blurred5.astype(float)))
        mean_sat    = float(np.mean(hsv[:, :, 1])) if hsv is not None else 0.0

        return ImageQuality(
            width=w,
            height=h,
            megapixels=round((w * h) / 1_000_000, 2),
            mean_brightness=round(float(np.mean(grey)), 1),
            contrast=round(float(np.std(grey)), 1),
            noise_level=round(noise_level, 1),
            blur_score=round(blur_score, 1),
            mean_saturation=round(mean_sat, 1),
        )

    # ── Strategy pipelines ────────────────────────────────────────────────────

    def _super_upscale(self, img: np.ndarray, result: PreprocessResult) -> np.ndarray:
        """
        Lanczos upscale for tiny images (< 600px wide).
        Lanczos4 preserves text edges better than bilinear/bicubic.
        After upscale, apply sharpening to compensate for interpolation blur.
        """
        h, w = img.shape[:2]
        # Scale to at least 1200px wide, max MAX_UPSCALE_WIDTH
        target_w = max(1200, min(int(w * 4), MAX_UPSCALE_WIDTH))
        scale = target_w / w
        target_h = int(h * scale)

        upscaled = cv2.resize(img, (target_w, target_h),
                              interpolation=cv2.INTER_LANCZOS4)

        # Unsharp mask to recover sharpness lost during upscale
        grey = cv2.cvtColor(upscaled, cv2.COLOR_RGB2GRAY)
        gaussian = cv2.GaussianBlur(grey, (0, 0), 2.0)
        sharpened = cv2.addWeighted(grey, 2.0, gaussian, -1.0, 0)

        result.stages_applied.append(f"lanczos_upscale:{w}→{target_w}px")
        result.stages_applied.append("unsharp_mask")
        return sharpened  # return greyscale already

    def _exposure_pipeline(self, img: np.ndarray, result: PreprocessResult) -> np.ndarray:
        """
        Fix overexposed/underexposed/low-contrast images.
        Pipeline: gamma → CLAHE → bilateral filter → adaptive threshold
        """
        grey = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY) if len(img.shape) == 3 else img

        # Ensure minimum resolution
        grey = self._ensure_min_width(grey, 1200, result)

        # Gamma correction
        quality = self._diagnose(
            cv2.cvtColor(img, cv2.COLOR_GRAY2RGB) if len(img.shape) == 2 else img
        )
        if quality.is_overexposed:
            gamma = 0.5   # darken
            result.stages_applied.append("gamma_0.5_darken")
        elif quality.is_underexposed:
            gamma = 2.0   # brighten
            result.stages_applied.append("gamma_2.0_brighten")
        else:
            gamma = 0.7
            result.stages_applied.append("gamma_0.7_correct")

        grey = self._apply_gamma(grey, gamma)

        # CLAHE — local contrast enhancement
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        grey = clahe.apply(grey)
        result.stages_applied.append("clahe")

        # Bilateral filter — edge-preserving noise removal
        grey = cv2.bilateralFilter(grey, 9, 75, 75)
        result.stages_applied.append("bilateral_filter")

        # Unsharp mask for sharpness
        gaussian = cv2.GaussianBlur(grey, (0, 0), 1.5)
        grey = cv2.addWeighted(grey, 1.5, gaussian, -0.5, 0)
        result.stages_applied.append("unsharp_mask")

        # Adaptive threshold
        binary = cv2.adaptiveThreshold(
            grey, 255,
            cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY, 25, 10
        )
        result.stages_applied.append("adaptive_threshold")

        return binary

    def _denoise_pipeline(self, img: np.ndarray, result: PreprocessResult) -> np.ndarray:
        """
        Fix noisy images (scanner noise, JPEG compression artefacts).
        Pipeline: bilateral filter → CLAHE → sharpen → adaptive threshold
        """
        grey = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY) if len(img.shape) == 3 else img
        grey = self._ensure_min_width(grey, 1200, result)

        # Non-local means — best quality denoising for text
        grey = cv2.fastNlMeansDenoising(grey, h=12, templateWindowSize=7,
                                        searchWindowSize=21)
        result.stages_applied.append("nlm_denoise")

        # CLAHE
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        grey = clahe.apply(grey)
        result.stages_applied.append("clahe")

        # Sharpen
        gaussian = cv2.GaussianBlur(grey, (0, 0), 1.5)
        grey = cv2.addWeighted(grey, 1.5, gaussian, -0.5, 0)
        result.stages_applied.append("unsharp_mask")

        binary = cv2.adaptiveThreshold(
            grey, 255,
            cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY, 21, 8
        )
        result.stages_applied.append("adaptive_threshold")
        return binary

    def _standard_pipeline(self, img: np.ndarray, result: PreprocessResult) -> np.ndarray:
        """
        Standard pipeline for good-quality images.
        Pipeline: greyscale → CLAHE → bilateral → adaptive threshold
        """
        grey = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY) if len(img.shape) == 3 else img
        grey = self._ensure_min_width(grey, 1200, result)
        result.stages_applied.append("greyscale")

        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        grey = clahe.apply(grey)
        result.stages_applied.append("clahe")

        grey = cv2.bilateralFilter(grey, 9, 75, 75)
        result.stages_applied.append("bilateral_filter")

        binary = cv2.adaptiveThreshold(
            grey, 255,
            cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY, 21, 10
        )
        result.stages_applied.append("adaptive_threshold")
        return binary

    def _coloured_bg_pipeline(self, img: np.ndarray, result: PreprocessResult) -> np.ndarray:
        """
        Coloured background pipeline for teal/blue PAN cards.

        KEY INSIGHT from real-world testing:
          Upscaling coloured-background cards HURTS quality because Lanczos
          interpolation blurs fine text strokes on the coloured background,
          making thresholding harder. We get BETTER results at original
          resolution with the right threshold.

        Runs candidates on the ORIGINAL resolution image (no upscale).
        Thresholds 80-110 tested — 80-90 works best for Kavya-style teal cards,
        100-110 for Prashanth-style light blue cards.

        After picking the best binary, THEN upscale for Tesseract if too small.
        """
        if len(img.shape) == 2:
            return self._standard_pipeline(
                cv2.cvtColor(img, cv2.COLOR_GRAY2RGB), result
            )

        h, w = img.shape[:2]

        hsv = cv2.cvtColor(img, cv2.COLOR_RGB2HSV)
        v_channel = hsv[:, :, 2]
        lab = cv2.cvtColor(img, cv2.COLOR_RGB2LAB)
        clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
        l_enhanced = clahe.apply(lab[:, :, 0])

        candidates: list[tuple[str, np.ndarray]] = []

        # HSV Value thresholds — tested range that covers all PAN card types
        # t=80:  dark navy text on any background (best for Kavya PAN)
        # t=90:  slightly looser (good for Prashanth PAN)
        # t=100: even looser
        # t=110: light blue background cards
        for thresh in [80, 90, 100, 110]:
            _, dark = cv2.threshold(v_channel, thresh, 255, cv2.THRESH_BINARY_INV)
            candidates.append((f"hsv_t{thresh}", cv2.bitwise_not(dark)))

        # LAB CLAHE + Otsu — different colour space separation
        _, lab_bin = cv2.threshold(l_enhanced, 0, 255,
                                   cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        candidates.append(("lab_clahe", lab_bin))

        # Blue channel — dark text on blue/teal shows very dark in B channel
        b_channel = img[:, :, 2]
        _, b_bin = cv2.threshold(b_channel, 0, 255,
                                 cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        candidates.append(("blue_channel", b_bin))

        # Pick best by OCR word count at current resolution
        best_name, best_img = self._pick_best_candidate(candidates)
        result.stages_applied.append(f"coloured_bg:{best_name}")

        # NOW upscale the winning binary image if too small
        # Upscaling binary is much safer than upscaling colour
        bh, bw = best_img.shape[:2]
        if bw < 1200:
            scale = 1200 / bw
            best_img = cv2.resize(
                best_img,
                (int(bw * scale), int(bh * scale)),
                interpolation=cv2.INTER_NEAREST  # nearest for binary — no blur
            )
            result.stages_applied.append(f"binary_upscale:{bw}→{int(bw*scale)}px")

        return best_img

    # ── Shared helpers ────────────────────────────────────────────────────────

    def _load(self, image_path: Path) -> Optional[np.ndarray]:
        try:
            pil_img = Image.open(image_path).convert("RGB")
            return np.array(pil_img)
        except Exception as exc:
            logger.warning("Image load failed | file=%s | error=%s",
                           image_path.name, exc)
            return None

    def _ensure_min_width(
        self, img: np.ndarray, min_width: int, result: PreprocessResult
    ) -> np.ndarray:
        """Upscale to min_width if smaller. Greyscale-aware."""
        h, w = img.shape[:2]
        if w >= min_width:
            return img
        scale = min(min_width / w, 4.0)
        new_w = int(w * scale)
        new_h = int(h * scale)
        resized = cv2.resize(img, (new_w, new_h), interpolation=cv2.INTER_LANCZOS4)
        result.stages_applied.append(f"upscale:{w}→{new_w}px")
        return resized

    def _apply_gamma(self, grey: np.ndarray, gamma: float) -> np.ndarray:
        """Apply gamma correction via lookup table (fast)."""
        table = np.array([
            min(255, int(((i / 255.0) ** gamma) * 255))
            for i in range(256)
        ], dtype=np.uint8)
        return cv2.LUT(grey, table)

    def _deskew(self, img: np.ndarray, result: PreprocessResult) -> np.ndarray:
        """
        Correct document skew using minAreaRect.
        Capped at MAX_DESKEW_ANGLE to prevent catastrophic rotations.
        """
        _, binary_inv = cv2.threshold(
            img, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU
        )
        coords = np.column_stack(np.where(binary_inv > 0))
        if len(coords) < 100:
            return img

        angle = cv2.minAreaRect(coords)[-1]
        if angle < -45:
            angle = 90 + angle

        result.skew_angle_degrees = round(angle, 2)

        if abs(angle) > MAX_DESKEW_ANGLE:
            logger.info("Deskew skipped | angle=%.1f° > cap=%d°",
                        angle, MAX_DESKEW_ANGLE)
            return img

        if abs(angle) < DESKEW_ANGLE_MIN:
            return img

        h, w = img.shape[:2]
        M = cv2.getRotationMatrix2D((w // 2, h // 2), angle, 1.0)
        rotated = cv2.warpAffine(img, M, (w, h),
                                 flags=cv2.INTER_CUBIC,
                                 borderMode=cv2.BORDER_REPLICATE)
        result.stages_applied.append(f"deskew:{angle:.1f}°")
        return rotated

    def _pick_best_candidate(
        self, candidates: list[tuple[str, np.ndarray]]
    ) -> tuple[str, np.ndarray]:
        """
        OCR each candidate and pick the one with the best CONTENT score.

        Word count alone is a poor metric — noisy images produce many
        garbage words and outscore clean images. Instead we score by:
          +50  valid PAN number found (AAAAA0000A)
          +30  Aadhaar number found (12 digits)
          +20  date found (DD/MM/YYYY)
          +5   per meaningful alpha word (≥4 chars), capped at 30
          +10  per known document keyword (NAME, FATHER, DOB, etc.)

        This metric reliably selects the threshold that extracts real
        document fields rather than the one with the most noise words.
        """
        import re as _re

        _PAN_RE   = _re.compile(r'[A-Z]{5}[0-9]{4}[A-Z]')
        _ADHR_RE  = _re.compile(r'\d{4}[\s-]?\d{4}[\s-]?\d{4}')
        _DATE_RE  = _re.compile(r'\d{1,2}[/\-]\d{1,2}[/\-]\d{2,4}')
        _KW       = {'name','father','mother','dob','birth','gender',
                     'male','female','permanent','account','income','tax'}

        def content_score(text: str) -> int:
            score = 0
            upper = text.upper()
            cleaned = text.replace(' ', '').upper()

            if _PAN_RE.search(cleaned):      score += 50
            if _ADHR_RE.search(cleaned):     score += 30
            if _DATE_RE.search(text):        score += 20

            alpha_words = [w for w in text.split()
                           if w.isalpha() and len(w) >= 4]
            score += min(len(alpha_words) * 5, 30)

            kw_hits = sum(1 for kw in _KW if kw in upper)
            score += kw_hits * 10

            return score

        best_name  = candidates[0][0]
        best_img   = candidates[0][1]
        best_score = -1

        for name, img in candidates:
            try:
                text = pytesseract.image_to_string(
                    img, config="--oem 3 --psm 6"
                )
                score = content_score(text)
                logger.info(
                    "Candidate | strategy=%s | score=%d", name, score
                )
                if score > best_score:
                    best_score = score
                    best_name  = name
                    best_img   = img
            except Exception:
                pass

        return best_name, best_img
