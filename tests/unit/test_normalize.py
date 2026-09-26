from datetime import date

import pytest

from sop_agent.domain.enums import IdentityField
from sop_agent.domain.normalize import (
    normalize_dob,
    normalize_email,
    normalize_id_last4,
    normalize_identity_field,
    normalize_name,
    normalize_phone,
    normalize_policy_number,
)

TODAY = date(2026, 3, 10)


@pytest.mark.parametrize(
    "raw",
    ["Margaret Chen", "  margaret   CHEN ", "Chen, Margaret", "Margaret Chen.", "MARGARET\tchen"],
)
def test_name_normalization_handles_order_punctuation_and_case(raw: str) -> None:
    assert normalize_name(raw) == "margaret chen"


def test_name_hyphen_splits_tokens_and_apostrophe_is_dropped() -> None:
    assert normalize_name("Ya-Wen Li") == "ya wen li"
    assert normalize_name("Sean O'Brien") == "sean obrien"
    assert normalize_name("Sean O\N{RIGHT SINGLE QUOTATION MARK}Brien") == "sean obrien"


def test_name_nfkc_folds_fullwidth_characters() -> None:
    fullwidth_ma = "\N{FULLWIDTH LATIN CAPITAL LETTER M}\N{FULLWIDTH LATIN SMALL LETTER A}"
    assert normalize_name(f"{fullwidth_ma} Tian") == "ma tian"


@pytest.mark.parametrize("raw", ["Margaret", "", "   ", "!!"])
def test_v7_name_needs_at_least_two_tokens(raw: str) -> None:
    assert normalize_name(raw) is None


@pytest.mark.parametrize(
    "raw",
    [
        "1985-03-15",
        "1985/3/15",
        "March 15, 1985",
        "March 15th 1985",
        "march 15th, 1985",
        "Mar 15 1985",
        "Mar. 15, 1985",
        "15 March 1985",
        "15th of March, 1985",
        "03/15/1985",
        "3-15-1985",
        "03.15.1985",
        "3/15/85",
    ],
)
def test_dob_accepts_common_formats(raw: str) -> None:
    assert normalize_dob(raw, TODAY) == "1985-03-15"


def test_dob_ambiguous_numeric_date_is_read_us_style() -> None:
    assert normalize_dob("04/05/1990", TODAY) == "1990-04-05"


def test_dob_unambiguous_day_first_numeric_date_is_accepted() -> None:
    assert normalize_dob("15/03/1985", TODAY) == "1985-03-15"


@pytest.mark.parametrize(
    ("raw", "expected"), [("3/15/26", None), ("1/2/26", "2026-01-02"), ("7/4/27", "1927-07-04")]
)
def test_dob_two_digit_year_uses_current_year_as_cutoff(raw: str, expected: str | None) -> None:
    # 26 is not after the current two-digit year, so it becomes 2026; 3/15/2026 is after TODAY, so invalid.
    assert normalize_dob(raw, TODAY) == expected


@pytest.mark.parametrize("raw", ["2027-01-01", "2026-03-11"])
def test_v7_dob_in_the_future_is_invalid(raw: str) -> None:
    assert normalize_dob(raw, TODAY) is None


@pytest.mark.parametrize(
    "raw", ["1899-12-31", "1985-02-30", "March 1985", "1985", "March 15", "15/15/1985", "soon"]
)
def test_v7_dob_incomplete_impossible_or_too_old_is_invalid(raw: str) -> None:
    assert normalize_dob(raw, TODAY) is None


@pytest.mark.parametrize(
    "raw",
    [
        "+16505212836",
        "16505212836",
        "650-521-2836",
        "(650) 521-2836",
        "650.521.2836",
        "650 521 2836",
        "+1 (650) 521-2836",
    ],
)
def test_phone_normalizes_to_ten_digits(raw: str) -> None:
    assert normalize_phone(raw) == "6505212836"


@pytest.mark.parametrize("raw", ["521-2836", "26505212836", "650521283", "+44 20 7946 0958 1", ""])
def test_v7_phone_with_wrong_digit_count_is_invalid(raw: str) -> None:
    assert normalize_phone(raw) is None


def test_email_is_trimmed_and_casefolded() -> None:
    assert normalize_email("  Margaret@Email.COM ") == "margaret@email.com"


@pytest.mark.parametrize("raw", ["margaret", "margaret@", "@email.com", "margaret@email", "a b@email.com"])
def test_v7_email_basic_format_check(raw: str) -> None:
    assert normalize_email(raw) is None


@pytest.mark.parametrize("raw", ["4472", " 4472 ", "44-72", "44 72"])
def test_id_last4_keeps_digits(raw: str) -> None:
    assert normalize_id_last4(raw) == "4472"


@pytest.mark.parametrize("raw", ["44", "447", "44721", "123-45-4472", "abcd", ""])
def test_v7_id_last4_with_wrong_length_is_invalid(raw: str) -> None:
    assert normalize_id_last4(raw) is None


@pytest.mark.parametrize("raw", [" pol-9921 ", "POL-9921", "pol 9921", "POL9921", "pol_9921", "P.O.L. 9921"])
def test_v5_policy_number_keeps_only_letters_and_digits(raw: str) -> None:
    assert normalize_policy_number(raw) == "POL9921"


@pytest.mark.parametrize("raw", ["", "   ", "--"])
def test_v7_empty_policy_number_is_invalid(raw: str) -> None:
    assert normalize_policy_number(raw) is None


@pytest.mark.parametrize(
    ("field", "raw", "expected"),
    [
        (IdentityField.FULL_NAME, "Chen, Margaret", "margaret chen"),
        (IdentityField.DOB, "March 15, 1985", "1985-03-15"),
        (IdentityField.PHONE, "(650) 521-2836", "6505212836"),
        (IdentityField.EMAIL, "M@E.com", "m@e.com"),
        (IdentityField.ID_LAST4, "4472", "4472"),
    ],
)
def test_normalize_identity_field_dispatches_per_field(field: IdentityField, raw: str, expected: str) -> None:
    assert normalize_identity_field(field, raw, TODAY) == expected
