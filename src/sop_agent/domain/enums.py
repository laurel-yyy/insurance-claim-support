"""Closed vocabularies shared by every layer.

StrEnum everywhere so values serialize cleanly and no module relies on magic strings.
"""

from enum import StrEnum


class Phase(StrEnum):
    """Top-level SOP phases (SPEC §7.1)."""

    VERIFY_ID = "VERIFY_ID"
    RESOLVE_INTENT = "RESOLVE_INTENT"
    PROCESS_CASE = "PROCESS_CASE"
    POST_PROCESS = "POST_PROCESS"
    ESCALATED = "ESCALATED"
    ENDED = "ENDED"


class VerifyStage(StrEnum):
    """Sub-stages inside VERIFY_ID; the last two exist only for representative calls (§8.1.7)."""

    IDENTITY = "identity"
    AUTHORIZATION = "authorization"
    CONSENT = "consent"


class Path(StrEnum):
    """Bounded workflow paths (§8.2.1). The first five match the guideline's intent_hints."""

    STATUS_INQUIRY = "status_inquiry"
    DENIAL_QUESTION = "denial_question"
    DOCUMENT_SUBMISSION = "document_submission"
    NEXT_STEPS = "next_steps"
    GENERAL_CLAIM_QUESTION = "general_claim_question"
    GENERAL_INSURANCE_QUESTION = "general_insurance_question"
    HUMAN_HANDOFF = "human_handoff"


class ClaimStatus(StrEnum):
    """Known claim statuses; anything else maps to OTHER so swapped datasets still load (§6.2.5)."""

    OPEN = "open"
    CLOSED = "closed"
    DENIED = "denied"
    OTHER = "other"

    @classmethod
    def from_raw(cls, raw: str) -> "ClaimStatus":
        """Map a raw data value to a status, tolerating unknown values instead of failing."""
        value = raw.strip().casefold()
        for member in cls:
            if member is not cls.OTHER and member.value == value:
                return member
        return cls.OTHER


class IdentityField(StrEnum):
    """PII fields that count toward identity verification (V1). policy_number is deliberately absent."""

    FULL_NAME = "full_name"
    DOB = "dob"
    PHONE = "phone"
    EMAIL = "email"
    ID_LAST4 = "id_last4"


class CallerRole(StrEnum):
    """Who is calling (A1). Without a third-party cue the caller is treated as the policyholder."""

    POLICYHOLDER = "policyholder"
    AUTHORIZED_REPRESENTATIVE = "authorized_representative"
    UNKNOWN = "unknown"
