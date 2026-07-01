"""
tests/unit/test_text_normalizer.py
-----------------------------------
Unit tests for text_normalizer.py.

All functions are pure — no mocks, no I/O, no external dependencies.
Tests cover happy path, edge cases, and real-world OCR garbage inputs.
"""

import pytest
from backend.app.utils.text_normalizer import (
    clean_ocr_line,
    extract_year,
    normalize_aadhaar,
    normalize_dob,
    normalize_gender,
    normalize_name,
    normalize_pan,
    normalize_text,
)


# ── normalize_text ────────────────────────────────────────────────────────────

class TestNormalizeText:
    def test_collapses_whitespace(self):
        assert normalize_text("Ravi   Kumar") == "Ravi Kumar"

    def test_strips_leading_trailing(self):
        assert normalize_text("  hello  ") == "hello"

    def test_empty_string(self):
        assert normalize_text("") == ""

    def test_newlines_collapsed(self):
        assert normalize_text("line1\nline2") == "line1 line2"

    def test_tabs_collapsed(self):
        assert normalize_text("col1\tcol2") == "col1 col2"


# ── normalize_name ────────────────────────────────────────────────────────────

class TestNormalizeName:
    def test_uppercase_to_title(self):
        assert normalize_name("RAVI KUMAR") == "Ravi Kumar"

    def test_lowercase_to_title(self):
        assert normalize_name("ravi kumar") == "Ravi Kumar"

    def test_strips_digits(self):
        # OCR sometimes picks up nearby digits
        result = normalize_name("RAVI 8 KUMAR")
        assert "8" not in result

    def test_strips_special_chars(self):
        result = normalize_name("RAVI@KUMAR!")
        assert "@" not in result
        assert "!" not in result

    def test_too_short_returns_none(self):
        assert normalize_name("A") is None

    def test_empty_returns_none(self):
        assert normalize_name("") is None

    def test_name_with_dot(self):
        # e.g. "S. Kumar" — dots in names are valid
        result = normalize_name("S. Kumar")
        assert result is not None
        assert "Kumar" in result

    def test_three_word_name(self):
        result = normalize_name("VENKATA RAMA RAO")
        assert result == "Venkata Rama Rao"


# ── normalize_aadhaar ─────────────────────────────────────────────────────────

class TestNormalizeAadhaar:
    def test_clean_12_digits(self):
        assert normalize_aadhaar("123456789012") == "123456789012"

    def test_spaced_format(self):
        assert normalize_aadhaar("1234 5678 9012") == "123456789012"

    def test_dashed_format(self):
        assert normalize_aadhaar("1234-5678-9012") == "123456789012"

    def test_ocr_O_corrected_to_0(self):
        # OCR reads '0' as 'O' or 'o'
        assert normalize_aadhaar("123O 5678 9O12") == "123056789012"

    def test_ocr_l_corrected_to_1(self):
        assert normalize_aadhaar("l234 5678 90l2") == "123456789012"

    def test_too_short_returns_none(self):
        assert normalize_aadhaar("1234 5678") is None

    def test_too_long_returns_none(self):
        assert normalize_aadhaar("1234 5678 9012 3456") is None

    def test_empty_returns_none(self):
        assert normalize_aadhaar("") is None

    def test_masked_aadhaar(self):
        # "XXXX XXXX 9012" → strip spaces → "XXXXXXXX9012" (12 chars) → valid
        result = normalize_aadhaar("XXXX XXXX 9012")
        assert result == "XXXXXXXX9012"

        # Already compact 12-char masked form
        result2 = normalize_aadhaar("XXXXXXXX9012")
        assert result2 == "XXXXXXXX9012"


# ── normalize_pan ─────────────────────────────────────────────────────────────

class TestNormalizePan:
    def test_valid_pan(self):
        assert normalize_pan("ABCDE1234F") == "ABCDE1234F"

    def test_lowercase_pan(self):
        assert normalize_pan("abcde1234f") == "ABCDE1234F"

    def test_pan_with_spaces(self):
        assert normalize_pan("ABCDE 1234 F") == "ABCDE1234F"

    def test_pan_embedded_in_text(self):
        # OCR might capture surrounding text
        result = normalize_pan("PAN: ABCDE1234F some noise")
        assert result == "ABCDE1234F"

    def test_invalid_pan_returns_none(self):
        assert normalize_pan("ABCDE123") is None  # too short

    def test_empty_returns_none(self):
        assert normalize_pan("") is None

    def test_all_letters_invalid(self):
        assert normalize_pan("ABCDEFGHIJ") is None

    def test_pan_format_strict(self):
        # First 5 must be letters, next 4 digits, last 1 letter
        assert normalize_pan("12345ABCDF") is None


# ── normalize_dob ─────────────────────────────────────────────────────────────

class TestNormalizeDob:
    def test_slash_format(self):
        assert normalize_dob("15/05/1990") == "1990-05-15"

    def test_dash_format(self):
        assert normalize_dob("15-05-1990") == "1990-05-15"

    def test_dot_format(self):
        assert normalize_dob("15.05.1990") == "1990-05-15"

    def test_iso_format_passthrough(self):
        assert normalize_dob("1990-05-15") == "1990-05-15"

    def test_month_name_format(self):
        assert normalize_dob("15 May 1990") == "1990-05-15"

    def test_abbreviated_month(self):
        assert normalize_dob("15 Jan 1985") == "1985-01-15"

    def test_invalid_returns_none(self):
        assert normalize_dob("not a date") is None

    def test_empty_returns_none(self):
        assert normalize_dob("") is None

    def test_future_year_returns_none(self):
        # Year 2090 is not a valid birth year
        assert normalize_dob("15/05/2090") is None

    def test_two_digit_year(self):
        result = normalize_dob("15/05/90")
        assert result is not None
        assert result.endswith("-05-15")


# ── normalize_gender ──────────────────────────────────────────────────────────

class TestNormalizeGender:
    def test_male_uppercase(self):
        assert normalize_gender("MALE") == "Male"

    def test_female_uppercase(self):
        assert normalize_gender("FEMALE") == "Female"

    def test_m_shorthand(self):
        assert normalize_gender("M") == "Male"

    def test_f_shorthand(self):
        assert normalize_gender("F") == "Female"

    def test_hindi_male(self):
        assert normalize_gender("पुरुष") == "Male"

    def test_hindi_female(self):
        assert normalize_gender("महिला") == "Female"

    def test_case_insensitive(self):
        assert normalize_gender("male") == "Male"
        assert normalize_gender("female") == "Female"

    def test_ocr_space_in_middle(self):
        # OCR sometimes splits "Male" into "Mal e"
        assert normalize_gender("Mal e") == "Male"

    def test_empty_returns_none(self):
        assert normalize_gender("") is None


# ── extract_year ──────────────────────────────────────────────────────────────

class TestExtractYear:
    def test_four_digit_year(self):
        assert extract_year("DOB: 15/05/1990") == "1990"

    def test_year_alone(self):
        assert extract_year("1985") == "1985"

    def test_no_year_returns_none(self):
        assert extract_year("no year here") is None

    def test_future_year_not_matched(self):
        # 2090 is outside the pattern range (19xx or 200x-202x)
        assert extract_year("2090") is None

    def test_prefers_birth_year_range(self):
        result = extract_year("Year: 1995")
        assert result == "1995"


# ── clean_ocr_line ────────────────────────────────────────────────────────────

class TestCleanOcrLine:
    def test_normal_line_unchanged(self):
        result = clean_ocr_line("Ravi Kumar")
        assert result == "Ravi Kumar"

    def test_punctuation_only_becomes_empty(self):
        result = clean_ocr_line("... --- |||")
        assert result == ""

    def test_whitespace_only_becomes_empty(self):
        result = clean_ocr_line("   ")
        assert result == ""

    def test_mixed_content_kept(self):
        result = clean_ocr_line("Name: Ravi Kumar")
        assert "Ravi Kumar" in result
