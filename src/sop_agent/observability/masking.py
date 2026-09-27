"""PII masking shared by logs, traces and debug views (SPEC §16, INV-9).

DOB -> ••••-••-••, ID number -> ••••, phone -> •••-•••-••36, email -> m•••••••@email.com, API keys never appear.
"""

import re
from collections.abc import Mapping
from typing import Any

DOT = "\N{BULLET}"
MASKED_DOB = f"{DOT * 4}-{DOT * 2}-{DOT * 2}"
MASKED_ID = DOT * 4
REDACTED_KEY = "[redacted]"

_API_KEY = re.compile(r"sk-ant-[A-Za-z0-9_\-]+")
_EMAIL = re.compile(r"([A-Za-z0-9._%+-])([A-Za-z0-9._%+-]*)(@[A-Za-z0-9.-]+\.[A-Za-z]{2,})")
_ISO_DATE = re.compile(r"\b(19|20)\d{2}-\d{2}-\d{2}\b")
_PHONE = re.compile(r"(?<![\d-])(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?(\d{2})(\d{2})(?![\d-])")


def mask_email(email: str) -> str:
    local, _, domain = email.partition("@")
    if not domain:
        return DOT * 8
    return f"{local[:1]}{DOT * max(len(local) - 1, 1)}@{domain}"


def mask_phone(phone: str) -> str:
    digits = re.sub(r"\D", "", phone)
    return f"{DOT * 3}-{DOT * 3}-{DOT * 2}{digits[-2:]}" if len(digits) >= 2 else DOT * 10


def mask_text(text: str) -> str:
    """Mask PII that may appear in free text (caller messages, replies) before it's logged or traced."""
    text = _API_KEY.sub(REDACTED_KEY, text)
    text = _EMAIL.sub(lambda m: mask_email(m.group(0)), text)
    text = _ISO_DATE.sub(MASKED_DOB, text)
    text = _PHONE.sub(lambda m: f"{DOT * 3}-{DOT * 3}-{DOT * 2}{m.group(2)}", text)
    return re.sub(r"(?<![\d-])\d{4}(?![\d-])", MASKED_ID, text)


def mask_value(key: str, value: Any) -> Any:
    lowered = key.casefold()
    if isinstance(value, str):
        if "key" in lowered:
            return REDACTED_KEY
        if lowered == "dob":
            return MASKED_DOB
        if lowered == "id_last4":
            return MASKED_ID
        if lowered == "phone":
            return mask_phone(value)
        if lowered in ("email", "to", "target", "summary_email"):
            return mask_email(value)
        return mask_text(value)
    return mask_data(value)


def mask_data(data: Any) -> Any:
    """Recursively mask a JSON-like structure by key name and by content."""
    if isinstance(data, Mapping):
        return {k: mask_value(str(k), v) for k, v in data.items()}
    if isinstance(data, list | tuple | set):
        return [mask_data(v) for v in data]
    if isinstance(data, str):
        return mask_text(data)
    return data
