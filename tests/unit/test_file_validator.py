"""
tests/unit/test_file_validator.py
----------------------------------
Unit tests for the synchronous parts of file_validator.py.

The async validate_and_save() is tested in integration tests (test_api.py)
where a real FastAPI test client is available.  Here we test the helpers.
"""

import os
import tempfile
from pathlib import Path

import pytest
from backend.app.utils.file_validator import (
    _check_magic_bytes,
    _get_extension,
    cleanup_temp_file,
)


class TestGetExtension:
    def test_jpeg(self):
        assert _get_extension("photo.jpeg") == "jpeg"

    def test_jpg(self):
        assert _get_extension("photo.jpg") == "jpg"

    def test_pdf(self):
        assert _get_extension("aadhaar.pdf") == "pdf"

    def test_uppercase_lowercased(self):
        assert _get_extension("AADHAAR.PDF") == "pdf"

    def test_no_extension(self):
        assert _get_extension("noextension") == ""

    def test_dotfile(self):
        # Python's Path(".hidden").suffix returns '' — no extension on dotfiles
        assert _get_extension(".hidden") == ""

    def test_multiple_dots(self):
        assert _get_extension("file.backup.pdf") == "pdf"


class TestCheckMagicBytes:
    def _write_tmp(self, content: bytes) -> Path:
        fd, path = tempfile.mkstemp()
        os.write(fd, content)
        os.close(fd)
        return Path(path)

    def test_valid_pdf(self):
        path = self._write_tmp(b"%PDF-1.4 rest of file")
        assert _check_magic_bytes(path, "pdf") is True
        path.unlink()

    def test_invalid_pdf(self):
        path = self._write_tmp(b"\xff\xd8\xff fake jpeg content")
        assert _check_magic_bytes(path, "pdf") is False
        path.unlink()

    def test_valid_jpeg(self):
        path = self._write_tmp(b"\xff\xd8\xff\xe0 fake jpeg")
        assert _check_magic_bytes(path, "jpg") is True
        path.unlink()

    def test_valid_png(self):
        path = self._write_tmp(b"\x89PNG\r\n\x1a\n fake png")
        assert _check_magic_bytes(path, "png") is True
        path.unlink()

    def test_unknown_extension_always_passes(self):
        path = self._write_tmp(b"anything here")
        assert _check_magic_bytes(path, "xyz") is True
        path.unlink()

    def test_missing_file_returns_false(self):
        missing = Path("/tmp/definitely_does_not_exist_xyz.pdf")
        assert _check_magic_bytes(missing, "pdf") is False


class TestCleanupTempFile:
    def test_deletes_existing_file(self):
        fd, path_str = tempfile.mkstemp()
        os.close(fd)
        path = Path(path_str)
        assert path.exists()
        cleanup_temp_file(path)
        assert not path.exists()

    def test_no_error_on_missing_file(self):
        # Should silently succeed even if file doesn't exist
        missing = Path("/tmp/smartfill_test_missing_xyz.tmp")
        cleanup_temp_file(missing)  # must not raise
