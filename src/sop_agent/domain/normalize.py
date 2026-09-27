"""PII normalization and format validation.

Every function returns the normalized value, or None when the input fails format validation (V7). Values that
come from the LLM are always re-run through these functions; the LLM's formatting is never trusted.
"""

import re
import unicodedata
from collections.abc import Callable
from datetime import date

from sop_agent.domain.enums import IdentityField

MIN_NAME_TOKENS = 2
MIN_DOB_YEAR = 1900
PHONE_DIGITS = 10
ID_DIGITS = 4

_MONTHS = {
    name: index
    for index, names in enumerate(
        (
            ("january", "jan"),
            ("february", "feb"),
            ("march", "mar"),
            ("april", "apr"),
            ("may",),
            ("june", "jun"),
            ("july", "jul"),
            ("august", "aug"),
            ("september", "sep", "sept"),
            ("october", "oct"),
            ("november", "nov"),
            ("december", "dec"),
        ),
        start=1,
    )
    for name in names
}
_MONTH_WORD = "|".join(sorted(_MONTHS, key=len, reverse=True))
_ORDINAL = r"(?:st|nd|rd|th)?"
_ISO = re.compile(r"^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})$")
_NUMERIC = re.compile(r"^(\d{1,2})[-/.](\d{1,2})[-/.](\d{2}|\d{4})$")
_MONTH_FIRST = re.compile(rf"^({_MONTH_WORD})\.?\s+(\d{{1,2}}){_ORDINAL},?\s+(\d{{2}}|\d{{4}})$")
_DAY_FIRST = re.compile(rf"^(\d{{1,2}}){_ORDINAL}\s+(?:of\s+)?({_MONTH_WORD})\.?,?\s+(\d{{2}}|\d{{4}})$")
_APOSTROPHES = frozenset({"'", "\N{RIGHT SINGLE QUOTATION MARK}"})
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def normalize_name(raw: str) -> str | None:
    """Casefolded tokens joined by single spaces; "Doe, Jane" becomes "jane doe"."""
    text = unicodedata.normalize("NFKC", raw).strip()
    if text.count(",") == 1:
        last, first = (part.strip() for part in text.split(","))
        if last and first:
            text = f"{first} {last}"
    cleaned = []
    for char in text:
        if char in _APOSTROPHES:
            continue
        category = unicodedata.category(char)
        cleaned.append(" " if category.startswith(("P", "S")) else char)
    tokens = "".join(cleaned).casefold().split()
    return " ".join(tokens) if len(tokens) >= MIN_NAME_TOKENS else None


def normalize_dob(raw: str, today: date) -> str | None:
    """ISO date string for a plausible date of birth, or None.

    Ambiguous numeric dates are read US-style (mm/dd); a two-digit year uses the current year as the cutoff.
    """
    parts = _dob_parts(unicodedata.normalize("NFKC", raw).strip().casefold())
    if parts is None:
        return None
    year, month, day = parts
    if year < 100:
        year += 2000 if year <= today.year % 100 else 1900
    try:
        parsed = date(year, month, day)
    except ValueError:
        return None
    if parsed.year < MIN_DOB_YEAR or parsed > today:
        return None
    return parsed.isoformat()


def _dob_parts(text: str) -> tuple[int, int, int] | None:
    if match := _ISO.match(text):
        return int(match[1]), int(match[2]), int(match[3])
    if match := _NUMERIC.match(text):
        first, second, year = int(match[1]), int(match[2]), int(match[3])
        if first > 12 >= second:  # Unambiguously day-first, e.g. 25/12/1990
            first, second = second, first
        return year, first, second
    if match := _MONTH_FIRST.match(text):
        return int(match[3]), _MONTHS[match[1]], int(match[2])
    if match := _DAY_FIRST.match(text):
        return int(match[3]), _MONTHS[match[2]], int(match[1])
    return None


def normalize_phone(raw: str) -> str | None:
    """Ten digits; a leading country code 1 on an 11-digit number is dropped (+15550100199 -> 5550100199)."""
    digits = re.sub(r"\D", "", raw)
    if len(digits) == PHONE_DIGITS + 1 and digits.startswith("1"):
        digits = digits[1:]
    return digits if len(digits) == PHONE_DIGITS else None


def normalize_email(raw: str) -> str | None:
    """Trimmed and casefolded, with a basic format check."""
    text = unicodedata.normalize("NFKC", raw).strip().casefold()
    return text if _EMAIL.match(text) else None


def normalize_id_last4(raw: str) -> str | None:
    """Exactly four digits. SSN and national ID are treated the same."""
    digits = re.sub(r"\D", "", raw)
    return digits if len(digits) == ID_DIGITS else None


def normalize_policy_number(raw: str) -> str | None:
    """Uppercased letters and digits only, so "pol 1234" and "POL-1234" agree (V2 signature, V5 lookup).

    Applied to both the caller's value and the record. Never counted as a match.
    """
    text = "".join(ch for ch in unicodedata.normalize("NFKC", raw) if ch.isalnum()).upper()
    return text or None


def normalize_identity_field(field: IdentityField, raw: str, today: date) -> str | None:
    """Dispatch to the normalizer for one identity field."""
    if field is IdentityField.DOB:
        return normalize_dob(raw, today)
    return _SIMPLE_NORMALIZERS[field](raw)


_SIMPLE_NORMALIZERS: dict[IdentityField, Callable[[str], str | None]] = {
    IdentityField.FULL_NAME: normalize_name,
    IdentityField.PHONE: normalize_phone,
    IdentityField.EMAIL: normalize_email,
    IdentityField.ID_LAST4: normalize_id_last4,
}
