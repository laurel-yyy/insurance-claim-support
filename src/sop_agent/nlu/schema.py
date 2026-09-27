"""NLU domain format (SPEC §10.3.1).

`NLUResult` is what memory merge and the policy consume: optional fields, where None or empty means "not
mentioned". Identity values stay raw here; memory merge re-normalizes them (code never trusts LLM formatting).
The LLM wire schema lives in `nlu/wire.py` and its conversion in `nlu/convert.py`.
"""

from enum import StrEnum

from pydantic import BaseModel, Field

from sop_agent.domain.dates import DateHint
from sop_agent.domain.enums import CallerRole, ClaimStatus, EmotionLabel, IdentityField, Path


class ValueSource(StrEnum):
    """Which extractor produced a value (§3.1 steps 1-2)."""

    REGEX = "regex"
    LLM = "llm"
    BOTH = "both"


class DialogAct(StrEnum):
    PROVIDE_IDENTITY = "provide_identity"
    STATE_NEED = "state_need"
    ASK_QUESTION = "ask_question"
    CONFIRM = "confirm"
    DENY = "deny"
    CORRECT = "correct"
    REFUSE = "refuse"
    REQUEST_LIVE_AGENT = "request_live_agent"
    DONE = "done"
    THANKS = "thanks"
    GREETING = "greeting"
    COMPLAIN = "complain"
    OFF_TOPIC = "off_topic"
    OTHER = "other"


class IdKind(StrEnum):
    SSN = "ssn"
    NATIONAL_ID = "national_id"
    UNSPECIFIED = "unspecified"


class Scope(StrEnum):
    IN_SCOPE = "in_scope"
    OUT_OF_SCOPE = "out_of_scope"
    MIXED = "mixed"


class Confirmation(StrEnum):
    YES = "yes"
    NO = "no"
    UNCLEAR = "unclear"


class QuestionKind(StrEnum):
    """Whether answering needs record data. A language judgment, so the Extractor labels it (§9.1.2)."""

    ACCOUNT = "account"
    GENERAL = "general"
    PROCESS = "process"
    OUT_OF_SCOPE = "out_of_scope"


class Question(BaseModel):
    """One question the caller asked. `kind` None means the label is missing: treated as ACCOUNT."""

    text: str
    kind: QuestionKind | None = None


class FieldMention(BaseModel):
    """A raw identity value as extracted, before code normalization."""

    raw: str
    source: ValueSource


class IntentScore(BaseModel):
    path: Path
    confidence: float


class NLUResult(BaseModel):
    """Everything extracted from one caller message, whatever the phase (write memory always).

    `text` is the raw message, used for deterministic phrase matching (K4); it is never shown to anyone.
    """

    text: str = ""
    dialog_acts: list[DialogAct] = Field(default_factory=list)
    identity: dict[IdentityField, FieldMention] = Field(default_factory=dict)
    id_kind: IdKind | None = None
    policy_number: str | None = None
    declined_fields: list[IdentityField] = Field(default_factory=list)
    caller_role: CallerRole = CallerRole.UNKNOWN
    representative_name: str | None = None
    relationship_to_policyholder: str | None = None
    case_id: str | None = None
    case_type: str | None = None
    claim_status: ClaimStatus | None = None
    date_hint: DateHint | None = None
    description_keywords: list[str] = Field(default_factory=list)
    intents: list[IntentScore] = Field(default_factory=list)
    followup_topics: list[str] = Field(default_factory=list)
    questions: list[Question] = Field(default_factory=list)
    unavailable_documents: list[str] = Field(default_factory=list)
    no_substitutes_available: bool = False
    scope: Scope = Scope.IN_SCOPE
    off_topic_subject: str | None = None
    emotion: EmotionLabel = EmotionLabel.NEUTRAL
    emotion_intensity: int = 0
    confirmation: Confirmation | None = None
    summary_email: str | None = None
    requests_live_agent: bool = False
    manipulation_attempt: bool = False
    abusive: bool = False
    safety_concern: bool = False
    degraded: bool = False  # The LLM failed; regex-only extraction (§10.3.2)
    conflicts: list[str] = Field(default_factory=list)  # Format fields where regex and LLM disagreed
