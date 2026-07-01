"""
utils/file_validator.py
-----------------------
Validates uploaded files at the API boundary — before any OCR or processing.

Responsibilities:
  1. Check file size against configured limit.
  2. Verify file extension is in the allowed list.
  3. Verify actual file magic bytes match the declared extension
     (prevents renamed-file attacks, e.g. a .exe renamed to .pdf).
  4. Write the file to a secure temp path for the pipeline to consume.
  5. Return a validated UploadedFile schema object.

This module has NO knowledge of OCR or parsing — it only cares whether
the file is safe and readable.
"""

import hashlib
import mimetypes
import os
import shutil
import tempfile
from pathlib import Path
from typing import Optional

from fastapi import UploadFile

from backend.app.core.config import get_settings
from backend.app.core.exceptions import (
    CorruptedFileError,
    FileTooLargeError,
    UnsupportedFileTypeError,
)
from backend.app.core.logging import get_logger
from backend.app.schemas.documents import DocumentType, UploadedFile

logger = get_logger(__name__)

# ── Magic byte signatures ─────────────────────────────────────────────────────
# We check the first N bytes of each file to confirm its actual type.

_MAGIC_BYTES: dict[str, list[bytes]] = {
    "pdf": [b"%PDF"],
    "png": [b"\x89PNG\r\n\x1a\n"],
    "jpg": [b"\xff\xd8\xff"],
    "jpeg": [b"\xff\xd8\xff"],
}

_EXTENSION_TO_MIME: dict[str, str] = {
    "pdf": "application/pdf",
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
}


def _get_extension(filename: str) -> str:
    """Extract and lowercase the file extension. Returns '' if none."""
    return Path(filename).suffix.lstrip(".").lower()


def _check_magic_bytes(file_path: Path, extension: str) -> bool:
    """
    Read the first 8 bytes of the file and compare against known signatures.

    Returns True if the file matches its declared extension.
    Returns True for unknown extensions (no signature defined) — we only
    reject definite mismatches.
    """
    signatures = _MAGIC_BYTES.get(extension)
    if not signatures:
        return True  # no signature known; allow through

    try:
        with open(file_path, "rb") as f:
            header = f.read(8)
        return any(header.startswith(sig) for sig in signatures)
    except OSError:
        return False


def _safe_temp_path(settings_tmp_dir: Path, original_filename: str) -> Path:
    """
    Generate a safe, unique temp file path that does NOT use the original
    filename (prevents path traversal attacks).

    Format: <tmp_dir>/<8-char-hash>_<extension>
    """
    ext = _get_extension(original_filename)
    unique_id = hashlib.md5(os.urandom(16)).hexdigest()[:8]
    safe_name = f"{unique_id}.{ext}" if ext else unique_id
    return settings_tmp_dir / safe_name


async def validate_and_save(
    upload: UploadFile,
    document_type: DocumentType,
) -> UploadedFile:
    """
    Validate an uploaded FastAPI UploadFile and write it to a secure temp path.

    Args:
        upload:        Raw FastAPI UploadFile from the multipart request.
        document_type: The DocumentType the operator declared this file as.

    Returns:
        UploadedFile schema with the validated temp path.

    Raises:
        FileTooLargeError:       File exceeds max_file_size_mb.
        UnsupportedFileTypeError: Extension not in allowed list.
        CorruptedFileError:      Magic bytes don't match extension, or read error.
    """
    settings = get_settings()

    filename = upload.filename or "unknown"
    extension = _get_extension(filename)

    logger.info(
        "Validating upload | document_type=%s | filename=%s | content_type=%s",
        document_type.value,
        filename,
        upload.content_type,
    )

    # 1. Extension check
    if extension not in settings.allowed_extensions:
        raise UnsupportedFileTypeError(
            f"File type '.{extension}' is not supported. "
            f"Allowed: {', '.join(settings.allowed_extensions)}",
            details={"extension": extension, "filename": filename},
        )

    # 2. Write to temp file in chunks (avoids loading entire file into RAM)
    tmp_path = _safe_temp_path(settings.tmp_dir, filename)
    total_bytes = 0

    try:
        with open(tmp_path, "wb") as tmp_file:
            while True:
                chunk = await upload.read(1024 * 64)  # 64 KB chunks
                if not chunk:
                    break
                total_bytes += len(chunk)

                if total_bytes > settings.max_file_size_bytes:
                    tmp_path.unlink(missing_ok=True)
                    raise FileTooLargeError(
                        f"File exceeds the {settings.max_file_size_mb} MB limit. "
                        f"Received {total_bytes / (1024 * 1024):.1f} MB so far.",
                        details={
                            "limit_mb": settings.max_file_size_mb,
                            "filename": filename,
                        },
                    )

                tmp_file.write(chunk)

    except (FileTooLargeError, UnsupportedFileTypeError):
        raise
    except OSError as exc:
        raise CorruptedFileError(
            f"Failed to write upload to disk: {exc}",
            details={"filename": filename},
        ) from exc

    # 3. Magic byte verification (after writing so we can seek from disk)
    if not _check_magic_bytes(tmp_path, extension):
        tmp_path.unlink(missing_ok=True)
        raise CorruptedFileError(
            f"File content does not match its extension '.{extension}'. "
            "The file may be corrupted or renamed.",
            details={"filename": filename, "extension": extension},
        )

    logger.info(
        "Upload validated | document_type=%s | size_bytes=%d | tmp=%s",
        document_type.value,
        total_bytes,
        tmp_path.name,   # log only the filename, not the full path
    )

    return UploadedFile(
        document_type=document_type,
        original_filename=filename,
        content_type=_EXTENSION_TO_MIME.get(extension, "application/octet-stream"),
        size_bytes=total_bytes,
        tmp_path=tmp_path,
    )


def cleanup_temp_file(tmp_path: Path) -> None:
    """
    Delete a temp file created during a session.

    Called in a finally block after the pipeline completes or fails.
    Errors here are logged but never raised — cleanup must not mask
    the original result or exception.
    """
    try:
        tmp_path.unlink(missing_ok=True)
        logger.debug("Temp file deleted | file=%s", tmp_path.name)
    except OSError as exc:
        logger.warning("Failed to delete temp file | file=%s | error=%s", tmp_path.name, exc)
