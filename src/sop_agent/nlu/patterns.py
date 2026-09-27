"""High-precision regex pre-extraction. Deterministic; runs before and without the LLM.

Only formats that are unambiguous in text are taken: emails, 10-11 digit phones, policy and claim IDs, and a DOB
or ID last four only when a cue ("DOB", "born", "last four", "SSN", ...) precedes it. Values are normalized.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date

from sop_agent.domain.enums import IdentityField
from sop_agent.domain.normalize import normalize_dob, normalize_email, normalize_phone

_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PHONE = re.compile(r"(?<![\d-])(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}(?![\d-])")
_POLICY = re.compile(r"\bPOL-\d+\b", re.IGNORECASE)
_CASE = re.compile(r"\bCL-?(\d+)\b", re.IGNORECASE)
_DOB_CUE = re.compile(
    r"\b(?:dob|d\.o\.b\.?|date of birth|birth ?date|birthday|born(?: on)?)\b", re.IGNORECASE
)
_ID_CUE = re.compile(
    r"\b(?:last\s*(?:four|4)(?:\s*digits)?|ssn|social security(?: number)?|national\s*id)\b", re.IGNORECASE
)
_FOUR_DIGITS = re.compile(r"(?<![\d-])(\d{4})(?![\d-])")
_CUE_FILLER = re.compile(r"^\s*(?:is|was|of|:|-)?\s*", re.IGNORECASE)
ID_WINDOW = 40
DOB_MAX_TOKENS = 5


@dataclass(frozen=True)
class PatternResult:
    identity: dict[IdentityField, str] = field(default_factory=dict)
    policy_number: str | None = None
    case_id: str | None = None


def extract_patterns(text: str, today: date) -> PatternResult:
    identity: dict[IdentityField, str] = {}
    if (email := _first_valid(_EMAIL.findall(text), normalize_email)) is not None:
        identity[IdentityField.EMAIL] = email
    if (phone := _first_valid(_PHONE.findall(text), normalize_phone)) is not None:
        identity[IdentityField.PHONE] = phone
    if (dob := _dob_after_cue(text, today)) is not None:
        identity[IdentityField.DOB] = dob
    if (last4 := _id_after_cue(text)) is not None:
        identity[IdentityField.ID_LAST4] = last4
    policy = _POLICY.search(text)
    case = _CASE.search(text)
    return PatternResult(
        identity=identity,
        policy_number=policy.group(0).upper() if policy else None,
        case_id=f"CL-{case.group(1)}" if case else None,
    )


def _first_valid(candidates: list[str], normalize: Callable[[str], str | None]) -> str | None:
    for candidate in candidates:
        if (value := normalize(candidate)) is not None:
            return value
    return None


def _dob_after_cue(text: str, today: date) -> str | None:
    """Take the longest run of up to 5 tokens after a DOB cue that parses as a valid date of birth."""
    for cue in _DOB_CUE.finditer(text):
        rest = _CUE_FILLER.sub("", text[cue.end() :], count=1)
        tokens = rest.split()
        for n in range(min(DOB_MAX_TOKENS, len(tokens)), 0, -1):
            candidate = " ".join(tokens[:n]).strip(" ,.;:!?")
            if (value := normalize_dob(candidate, today)) is not None:
                return value
    return None


def _id_after_cue(text: str) -> str | None:
    for cue in _ID_CUE.finditer(text):
        window = re.split(r"[.;!?]\s", text[cue.end() : cue.end() + ID_WINDOW], maxsplit=1)[0]
        if match := _FOUR_DIGITS.search(window):
            return match.group(1)
    return None
