"""
services/ocr/pdf_converter.py
------------------------------
Converts PDF documents to a list of numpy image arrays.

Primary method: PyMuPDF (fitz) — fast, no external binary required.
Fallback: pdf2image (requires poppler to be installed).

For Aadhaar and PAN cards:
  - The PDF is almost always a single page.
  - We render at 300 DPI to give Tesseract enough resolution.
  - We return ALL pages so multi-page documents (e.g. a 2-page resume)
    are handled correctly in future milestones.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image

from backend.app.core.logging import get_logger

logger = get_logger(__name__)

# Target render DPI — 300 gives Tesseract enough resolution for small fonts
RENDER_DPI = 300
# PyMuPDF uses a zoom matrix; 1.0 = 72 DPI, so 300/72 ≈ 4.17
_FITZ_ZOOM = RENDER_DPI / 72.0


def pdf_to_images(pdf_path: Path) -> list[np.ndarray]:
    """
    Convert all pages of a PDF to a list of numpy image arrays (RGB).

    Tries PyMuPDF first, falls back to pdf2image if fitz fails.

    Args:
        pdf_path: Absolute path to a PDF file.

    Returns:
        List of numpy arrays (one per page), shape (H, W, 3), dtype uint8.

    Raises:
        ValueError: If the PDF cannot be opened by either method.
    """
    logger.info("Converting PDF to images | file=%s", pdf_path.name)

    # ── Attempt 1: PyMuPDF ────────────────────────────────────────────────────
    try:
        import fitz  # PyMuPDF

        pages = _convert_with_fitz(pdf_path, fitz)
        logger.info(
            "PDF converted via PyMuPDF | file=%s | pages=%d",
            pdf_path.name, len(pages),
        )
        return pages

    except ImportError:
        logger.warning("PyMuPDF not available; trying pdf2image")
    except Exception as exc:
        logger.warning("PyMuPDF failed | error=%s | trying pdf2image", exc)

    # ── Attempt 2: pdf2image ──────────────────────────────────────────────────
    try:
        pages = _convert_with_pdf2image(pdf_path)
        logger.info(
            "PDF converted via pdf2image | file=%s | pages=%d",
            pdf_path.name, len(pages),
        )
        return pages

    except Exception as exc:
        raise ValueError(
            f"Could not convert PDF '{pdf_path.name}' to images. "
            f"Both PyMuPDF and pdf2image failed. Last error: {exc}"
        ) from exc


def _convert_with_fitz(pdf_path: Path, fitz) -> list[np.ndarray]:
    """Convert PDF pages using PyMuPDF (fitz)."""
    doc = fitz.open(str(pdf_path))
    pages: list[np.ndarray] = []

    matrix = fitz.Matrix(_FITZ_ZOOM, _FITZ_ZOOM)

    for page_num in range(len(doc)):
        page = doc[page_num]
        pixmap = page.get_pixmap(matrix=matrix, colorspace=fitz.csRGB)

        # Convert pixmap bytes → PIL Image → numpy array
        pil_img = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
        pages.append(np.array(pil_img))

    doc.close()
    return pages


def _convert_with_pdf2image(pdf_path: Path) -> list[np.ndarray]:
    """Convert PDF pages using pdf2image (requires poppler)."""
    from pdf2image import convert_from_path

    pil_images = convert_from_path(str(pdf_path), dpi=RENDER_DPI)
    return [np.array(img.convert("RGB")) for img in pil_images]
