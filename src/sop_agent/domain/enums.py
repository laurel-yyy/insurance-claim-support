"""Closed vocabularies shared by every layer.

StrEnum everywhere so values serialize cleanly and no module relies on magic strings.
"""

from enum import StrEnum


class Phase(StrEnum):
    """Top-level SOP phases."""

    VERIFY_ID = "VERIFY_ID"
    RESOLVE_INTENT = "RESOLVE_INTENT"
    PROCESS_CASE = "PROCESS_CASE"
    POST_PROCESS = "POST_PROCESS"
    ESCALATED = "ESCALATED"
    ENDED = "ENDED"


class VerifyStage(StrEnum):
    """Sub-stages inside VERIFY_ID; the last two exist only for representative calls."""

    IDENTITY = "identity"
    AUTHORIZATION = "authorization"
    CONSENT = "consent"


class Path(StrEnum):
    """Bounded workflow paths. The first five match the guideline's intent_hints."""

    STATUS_INQUIRY = "status_inquiry"
    DENIAL_QUESTION = "denial_question"
    DOCUMENT_SUBMISSION = "document_submission"
    NEXT_STEPS = "next_steps"
    GENERAL_CLAIM_QUESTION = "general_claim_question"
    GENERAL_INSURANCE_QUESTION = "general_insurance_question"
    HUMAN_HANDOFF = "human_handoff"


class ClaimStatus(StrEnum):
    """Known claim statuses; anything else maps to OTHER so swapped datasets still load."""

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


class EmotionLabel(StrEnum):
    """Emotion recognized in the latest message."""

    NEUTRAL = "neutral"
    FRUSTRATED = "frustrated"
    ANGRY = "angry"
    ANXIOUS = "anxious"
    CONFUSED = "confused"
    SAD = "sad"


class IdentityStatus(StrEnum):
    """Verification status. IDENTITY_VERIFIED is the representative flow's midpoint."""

    UNVERIFIED = "unverified"
    IDENTITY_VERIFIED = "identity_verified"
    VERIFIED = "verified"
    LOCKED = "locked"


class VerifiedAs(StrEnum):
    """Who completed verification."""

    POLICYHOLDER = "policyholder"
    REPRESENTATIVE = "representative"


class AuthorizationStatus(StrEnum):
    """Outcome of the representative authorization check (A3/A4)."""

    NOT_NEEDED = "not_needed"
    NEEDS_INFO = "needs_info"
    MATCHED = "matched"
    NOT_AUTHORIZED = "not_authorized"


class ConsentStatus(StrEnum):
    """Real-time policyholder consent status (A5/A6)."""

    NOT_REQUESTED = "not_requested"
    PENDING = "pending"
    APPROVED = "approved"
    DECLINED = "declined"
    TIMEOUT = "timeout"


class EscalationReason(StrEnum):
    """Why a session was handed to a live agent."""

    SAFETY = "SAFETY"
    CALLER_REQUEST = "CALLER_REQUEST"
    VERIFICATION_LOCKED = "VERIFICATION_LOCKED"
    OFF_TOPIC = "OFF_TOPIC"
    PERSUASION_EXHAUSTED = "PERSUASION_EXHAUSTED"
    ABUSE = "ABUSE"
    DOCUMENT_ALTERNATIVES_EXHAUSTED = "DOCUMENT_ALTERNATIVES_EXHAUSTED"


PATHS_NEEDING_CLAIM: frozenset[Path] = frozenset(
    {
        Path.STATUS_INQUIRY,
        Path.DENIAL_QUESTION,
        Path.DOCUMENT_SUBMISSION,
        Path.NEXT_STEPS,
        Path.GENERAL_CLAIM_QUESTION,
    }
)
