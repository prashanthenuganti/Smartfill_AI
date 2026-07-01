"""
services/parsers/base.py
------------------------
Abstract base class that every document parser must implement.

Enforces a consistent interface so the pipeline can call any parser
without knowing its internal logic. Adding a new document type in
Milestone 2 means implementing this interface — nothing else changes.
"""

from abc import ABC, abstractmethod

from backend.app.schemas.extraction import ExtractionResult
from backend.app.services.ocr.engine import OCRResult


class BaseDocumentParser(ABC):
    """
    Contract for all document parsers.

    Each parser receives an OCRResult and returns an ExtractionResult.
    Parsers must NEVER raise — all errors must be caught internally and
    reflected in the returned ExtractionResult's status and error fields.
    """

    @abstractmethod
    def parse(self, ocr_result: OCRResult) -> ExtractionResult:
        """
        Parse OCR output into a structured ExtractionResult.

        Args:
            ocr_result: Output from TesseractOCREngine.extract()

        Returns:
            ExtractionResult with populated fields and confidence scores.
            Status is "success", "partial", or "failed".
            Never raises an exception.
        """
        ...

    @property
    @abstractmethod
    def document_type(self) -> str:
        """Return the document type string this parser handles."""
        ...
