"""LLM-facing wire schemas (SPEC §10.2, §10.3.1, §8.2.6).

Every field is required and there are no Optional or union types: "", 0, "none", [] and false mean "not
mentioned". Enum strings are lowercased before validation, and unusable labels degrade per field instead of
failing the whole extraction. No customer data ever appears in these schemas.
"""

from enum import Enum, StrEnum
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, create_model, model_validator

from sop_agent.domain.enums import CallerRole, EmotionLabel, IdentityField, Path
from sop_agent.domain.models import DocumentGuideline
from sop_agent.nlu.schema import DialogAct, QuestionKind, Scope

NONE = "none"


class IdKindWire(StrEnum):
    SSN = "ssn"
    NATIONAL_ID = "national_id"
    UNSPECIFIED = "unspecified"
    NONE = "none"


class ClaimStatusHint(StrEnum):
    DENIED = "denied"
    CLOSED = "closed"
    OPEN = "open"
    NONE = "none"


class ConfirmationWire(StrEnum):
    YES = "yes"
    NO = "no"
    UNCLEAR = "unclear"
    NONE = "none"


class PathOrNone(StrEnum):
    STATUS_INQUIRY = Path.STATUS_INQUIRY.value
    DENIAL_QUESTION = Path.DENIAL_QUESTION.value
    DOCUMENT_SUBMISSION = Path.DOCUMENT_SUBMISSION.value
    NEXT_STEPS = Path.NEXT_STEPS.value
    GENERAL_CLAIM_QUESTION = Path.GENERAL_CLAIM_QUESTION.value
    GENERAL_INSURANCE_QUESTION = Path.GENERAL_INSURANCE_QUESTION.value
    HUMAN_HANDOFF = Path.HUMAN_HANDOFF.value
    NONE = "none"


def _lower(value: Any) -> Any:
    return value.strip().casefold() if isinstance(value, str) else value


def _scalar(value: Any, enum: type[Enum], default: str) -> Any:
    """Lowercase; an unknown label becomes the safe default for that field."""
    value = _lower(value)
    allowed = {m.value for m in enum}
    return value if value in allowed else default


def _filter(values: Any, allowed: set[str]) -> Any:
    """Lowercase list items and drop unknown labels."""
    if not isinstance(values, list):
        return values
    return [v for v in (_lower(x) for x in values) if v in allowed]


class _Lenient(BaseModel):
    model_config = ConfigDict(extra="ignore")


class IntentScoreWire(_Lenient):
    path: PathOrNone
    confidence: float

    @model_validator(mode="before")
    @classmethod
    def _labels(cls, data: Any) -> Any:
        if isinstance(data, dict):
            data = {**data, "path": _scalar(data.get("path"), PathOrNone, NONE)}
        return data


class QuestionWire(_Lenient):
    text: str
    kind: QuestionKind

    @model_validator(mode="before")
    @classmethod
    def _labels(cls, data: Any) -> Any:
        if isinstance(data, dict):  # A missing or unknown kind is treated as account
            data = {**data, "kind": _scalar(data.get("kind"), QuestionKind, QuestionKind.ACCOUNT.value)}
        return data


SCALAR_DEFAULTS: dict[str, tuple[type[Enum], str]] = {
    "id_kind": (IdKindWire, NONE),
    "caller_role": (CallerRole, CallerRole.UNKNOWN.value),
    "claim_status": (ClaimStatusHint, NONE),
    "scope": (Scope, Scope.IN_SCOPE.value),
    "emotion": (EmotionLabel, EmotionLabel.NEUTRAL.value),
    "confirmation": (ConfirmationWire, ConfirmationWire.UNCLEAR.value),
}
LIST_ENUMS: dict[str, type[Enum]] = {"dialog_acts": DialogAct, "declined_fields": IdentityField}


class NLUWireBase(_Lenient):
    """Fields shared by every dataset; `followup_topics` is added per guideline by build_nlu_wire()."""

    topic_names: ClassVar[frozenset[str]] = frozenset()

    dialog_acts: list[DialogAct]
    full_name: str
    dob: str
    phone: str
    email: str
    id_last4: str
    id_kind: IdKindWire
    policy_number: str
    declined_fields: list[IdentityField]
    caller_role: CallerRole
    representative_name: str
    relationship_to_policyholder: str
    case_id: str
    case_type: str
    claim_status: ClaimStatusHint
    date_year: int
    date_month: int
    date_day: int
    description_keywords: list[str]
    intents: list[IntentScoreWire]
    questions: list[QuestionWire]
    unavailable_documents: list[str]
    no_substitutes_available: bool
    scope: Scope
    off_topic_subject: str
    emotion: EmotionLabel
    emotion_intensity: int
    confirmation: ConfirmationWire
    summary_email: str
    requests_live_agent: bool
    manipulation_attempt: bool
    abusive: bool
    safety_concern: bool

    @model_validator(mode="before")
    @classmethod
    def _labels(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        data = dict(data)
        for name, (enum, default) in SCALAR_DEFAULTS.items():
            if name in data:
                data[name] = _scalar(data[name], enum, default)
        for name, enum in LIST_ENUMS.items():
            if name in data:
                data[name] = _filter(data[name], {m.value for m in enum})
        if "followup_topics" in data:
            data["followup_topics"] = _filter(data["followup_topics"], set(cls.topic_names))
        return data

    def topics(self) -> list[str]:
        return [str(getattr(t, "value", t)) for t in getattr(self, "followup_topics", [])]


def build_nlu_wire(guideline: DocumentGuideline) -> type[NLUWireBase]:
    """K7: the followup_topics enum comes from the loaded guidance, so a different dataset still works."""
    names = [t.topic.casefold() for t in guideline.followup_topics] or [NONE]
    topic_enum = StrEnum("FollowupTopicName", {f"T{i}": name for i, name in enumerate(dict.fromkeys(names))})  # type: ignore[misc]
    model: type[NLUWireBase] = create_model(
        "NLUWire",
        __base__=NLUWireBase,
        followup_topics=(list[topic_enum], ...),
    )
    model.topic_names = frozenset(names)
    return model


class SelectorWire(_Lenient):
    """ClaimSelector output. Candidate IDs go in the message, never in this schema (§8.2.6)."""

    case_id: str
    path: PathOrNone
    confidence: float

    @model_validator(mode="before")
    @classmethod
    def _labels(cls, data: Any) -> Any:
        if isinstance(data, dict):
            data = {**data, "path": _scalar(data.get("path"), PathOrNone, NONE)}
        return data
