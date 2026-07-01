"""
tests/unit/test_preprocessor.py
---------------------------------
Unit tests for the image preprocessor.

Uses synthetically generated numpy images — no real document scans needed.
Tests verify that each preprocessing stage runs without error and produces
a valid output image of the expected shape and dtype.
"""

import tempfile
from pathlib import Path

import cv2
import numpy as np
import pytest
from PIL import Image

from backend.app.services.ocr.preprocessor import (
    ImagePreprocessor,
    PreprocessResult,
    TARGET_WIDTH_PX,
)


def _make_grey_image(w: int = 800, h: int = 500) -> np.ndarray:
    """Create a synthetic greyscale test image with some text-like content."""
    img = np.ones((h, w), dtype=np.uint8) * 240  # light grey background
    # Add some dark rectangles to simulate text blocks
    cv2.rectangle(img, (50, 50), (400, 80), 30, -1)
    cv2.rectangle(img, (50, 100), (300, 130), 30, -1)
    cv2.rectangle(img, (50, 150), (350, 180), 30, -1)
    return img


def _make_rgb_image(w: int = 800, h: int = 500) -> np.ndarray:
    """Create a synthetic RGB test image."""
    img = np.ones((h, w, 3), dtype=np.uint8) * 240
    cv2.rectangle(img, (50, 50), (400, 80), (30, 30, 30), -1)
    return img


def _save_as_png(arr: np.ndarray) -> Path:
    """Save numpy array to a temp PNG file and return the path."""
    fd, path_str = tempfile.mkstemp(suffix=".png")
    path = Path(path_str)
    pil = Image.fromarray(arr if len(arr.shape) == 3 else arr)
    pil.save(path)
    return path


class TestImagePreprocessor:
    def setup_method(self):
        self.preprocessor = ImagePreprocessor()

    def test_process_returns_preprocess_result(self):
        img = _make_rgb_image()
        path = _save_as_png(img)
        try:
            result = self.preprocessor.process(path)
            assert isinstance(result, PreprocessResult)
        finally:
            path.unlink()

    def test_output_is_2d_greyscale(self):
        """After processing, image should be greyscale (2D array)."""
        img = _make_rgb_image()
        path = _save_as_png(img)
        try:
            result = self.preprocessor.process(path)
            assert len(result.image.shape) == 2, "Expected 2D greyscale array"
        finally:
            path.unlink()

    def test_output_dtype_uint8(self):
        img = _make_rgb_image()
        path = _save_as_png(img)
        try:
            result = self.preprocessor.process(path)
            assert result.image.dtype == np.uint8
        finally:
            path.unlink()

    def test_stages_applied_recorded(self):
        img = _make_rgb_image(w=400)  # smaller than TARGET_WIDTH_PX → resize triggered
        path = _save_as_png(img)
        try:
            result = self.preprocessor.process(path)
            assert len(result.stages_applied) > 0
            # Greyscale and binarise must always be applied
            stages_str = " ".join(result.stages_applied)
            assert "greyscale" in stages_str
            assert "adaptive_threshold" in stages_str
        finally:
            path.unlink()

    def test_small_image_is_upscaled(self):
        """Images narrower than TARGET_WIDTH_PX should be enlarged."""
        small_w = 400
        img = _make_rgb_image(w=small_w)
        path = _save_as_png(img)
        try:
            result = self.preprocessor.process(path)
            _, final_w = result.final_size
            assert final_w > small_w
        finally:
            path.unlink()

    def test_original_size_recorded(self):
        img = _make_rgb_image(w=600, h=400)
        path = _save_as_png(img)
        try:
            result = self.preprocessor.process(path)
            assert result.original_size == (600, 400)
        finally:
            path.unlink()

    def test_missing_file_raises_value_error(self):
        missing = Path("/tmp/smartfill_test_missing.png")
        with pytest.raises(ValueError, match="Could not load image"):
            self.preprocessor.process(missing)

    def test_process_from_array(self):
        """process_from_array should work identically to process() for numpy input."""
        img = _make_rgb_image()
        result = self.preprocessor.process_from_array(img)
        assert isinstance(result, PreprocessResult)
        assert len(result.image.shape) == 2  # greyscale output

    def test_already_greyscale_not_converted_again(self):
        """If image is already 2D greyscale, greyscale stage should still work."""
        grey = _make_grey_image()
        result = self.preprocessor.process_from_array(grey)
        assert len(result.image.shape) == 2

    def test_binarise_output_is_binary(self):
        """After binarisation, pixel values should only be 0 or 255."""
        img = _make_rgb_image()
        result = self.preprocessor.process_from_array(img)
        unique_values = np.unique(result.image)
        assert set(unique_values).issubset({0, 255}), (
            f"Expected only 0 and 255 in binarised image, got {unique_values}"
        )
