"""What the policy tells the rest of the pipeline each turn (SPEC §7.5).

The directive is the only channel from deterministic code to the responder; the LLM never writes one (INV-1).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from sop_agent.domain.enums import EmotionLabel, IdentityField, Path, Phase, VerifyStage
from sop_agent.sop.phases import Freedom

if TYPE_CHECKING:
    from sop_agent.memory.state import SessionState


class EventType(StrEnum):
    """Everything that can happen in a turn; used by the UI timeline and eval assertions."""

    # Identity and authorization
    IDENTITY_FIELD_CAPTURED = "IDENTITY_FIELD_CAPTURED"
    IDENTITY_FIELD_INVALID = "IDENTITY_FIELD_INVALID"
    IDENTITY_FIELD_DECLINED = "IDENTITY_FIELD_DECLINED"
    VERIFICATION_FAILED = "VERIFICATION_FAILED"
    VERIFICATION_LOCKED = "VERIFICATION_LOCKED"
    IDENTITY_VERIFIED = "IDENTITY_VERIFIED"
    VERIFIED = "VERIFIED"
    REP_NOT_AUTHORIZED = "REP_NOT_AUTHORIZED"
    CONSENT_REQUESTED = "CONSENT_REQUESTED"
    CONSENT_STATUS = "CONSENT_STATUS"
    CONSENT_APPROVED = "CONSENT_APPROVED"
    CONSENT_DECLINED = "CONSENT_DECLINED"
    CONSENT_TIMEOUT = "CONSENT_TIMEOUT"
    # Memory and resolution
    HINT_STORED = "HINT_STORED"
    QUESTION_DEFERRED = "QUESTION_DEFERRED"
    CLAIM_CANDIDATES = "CLAIM_CANDIDATES"
    CLAIM_RESOLVED = "CLAIM_RESOLVED"
    CLAIM_REJECTED_BY_CALLER = "CLAIM_REJECTED_BY_CALLER"
    PATH_SELECTED = "PATH_SELECTED"
    NO_CLAIMS_ON_FILE = "NO_CLAIMS_ON_FILE"
    # Flow and actions
    PHASE_CHANGED = "PHASE_CHANGED"
    TOOL_CALLED = "TOOL_CALLED"
    TOOL_DENIED = "TOOL_DENIED"
    ACTION_EXECUTED = "ACTION_EXECUTED"
    ACTION_FAILED = "ACTION_FAILED"
    OFF_TOPIC_DECLINED = "OFF_TOPIC_DECLINED"
    LIVE_AGENT_OFFERED = "LIVE_AGENT_OFFERED"
    ESCALATED = "ESCALATED"
    SUMMARY_DRAFTED = "SUMMARY_DRAFTED"
    EMAIL_OFFERED = "EMAIL_OFFERED"
    EMAIL_SENT = "EMAIL_SENT"
    EMAIL_SKIPPED = "EMAIL_SKIPPED"
    SESSION_ENDED = "SESSION_ENDED"
    # Reliability
    GUARD_BLOCKED = "GUARD_BLOCKED"
    LLM_FALLBACK = "LLM_FALLBACK"
    NLU_CONFLICT = "NLU_CONFLICT"


class Event(BaseModel):
    """One thing that happened. `data` holds field names, counts or IDs, never raw PII values (INV-9)."""

    type: EventType
    turn: int
    phase: Phase
    data: dict[str, Any] = Field(default_factory=dict)


class ActionKind(StrEnum):
    """Side effects only the ActionExecutor may run, after confirmation stored in state (INV-5)."""

    REQUEST_CONSENT = "REQUEST_CONSENT"
    SEND_SUMMARY_EMAIL = "SEND_SUMMARY_EMAIL"
    TRANSFER_TO_LIVE_AGENT = "TRANSFER_TO_LIVE_AGENT"


class PlannedAction(BaseModel):
    """An action the policy decided to run; preconditions are re-checked before execution."""

    kind: ActionKind
    params: dict[str, Any] = Field(default_factory=dict)


class ActionResult(BaseModel):
    """What the executor reports back for one planned action, so the policy can settle the turn."""

    action: PlannedAction
    ok: bool
    detail: str = ""


class PendingQuestionKind(StrEnum):
    """Yes/no-style questions whose answer only the asking phase may consume (§7.4)."""

    CHOOSE_CLAIM = "choose_claim"
    CONFIRM_CONSENT_REQUEST = "confirm_consent_request"
    OFFER_LIVE_AGENT = "offer_live_agent"
    ANYTHING_ELSE = "anything_else"
    OFFER_SUMMARY_EMAIL = "offer_summary_email"
    CONFIRM_ALT_EMAIL = "confirm_alt_email"


class PendingQuestion(BaseModel):
    """The open question and the action a "yes" would trigger (INV-6: only the latest offer counts)."""

    kind: PendingQuestionKind
    asked_in_phase: Phase
    asked_turn: int
    payload: dict[str, Any] = Field(default_factory=dict)
    on_yes: PlannedAction | None = None
    unclear_replies: int = 0


class AskFor(BaseModel):
    """Which identity fields to request and how many are still needed."""

    fields: list[IdentityField]
    count: int


class DeferredNote(BaseModel):
    """A question that can't be answered this turn, with the reasons-library key explaining why."""

    question: str
    reason_key: str


class DeclineInfo(BaseModel):
    """Out-of-scope topic and streak, so the responder can vary how it declines."""

    topic: str
    streak: int


class EmotionStrategy(StrEnum):
    """Reply order for an emotion (§9.3.2)."""

    ACKNOWLEDGE_EXPLAIN_OFFER_OPTIONS = "acknowledge_explain_offer_options"
    REASSURE_EXPLAIN_NEXT = "reassure_explain_next"
    SIMPLIFY_ONE_STEP = "simplify_one_step"
    CARE_THEN_GENTLE = "care_then_gentle"
    RESPECT_EXPLAIN_ALTERNATIVES = "respect_explain_alternatives"
    CALM_BOUNDARY = "calm_boundary"


class EmotionPlan(BaseModel):
    """Reply strategy chosen by code for the caller's emotion (§9.3.2)."""

    label: EmotionLabel
    intensity: int
    strategy: EmotionStrategy
    reason_key: str | None = None


class GroundingRequest(BaseModel):
    """Which guidance the ContextBuilder should render into grounding this turn (names only, no text).

    Topic triggering (K3/K4) depends on this turn's NLU, which the ContextBuilder doesn't see.
    """

    topics: list[str] = Field(default_factory=list)
    background_topics: list[str] = Field(default_factory=list)
    use_followup_fallback: bool = False
    alternatives_for: list[str] = Field(default_factory=list)
    skipped_topics: list[str] = Field(default_factory=list)  # K5: unknown placeholder; logged by the caller


class TurnDirective(BaseModel):
    """Everything the responder must do this turn; `fallback_reply` is the fail-closed answer (INV-8)."""

    phase: Phase
    verify_stage: VerifyStage | None
    freedom: Freedom
    events: list[Event] = Field(default_factory=list)
    must: list[str] = Field(default_factory=list)
    must_not: list[str] = Field(default_factory=list)
    ask_for: AskFor | None = None
    acknowledge: list[str] = Field(default_factory=list)
    answer_now: list[str] = Field(default_factory=list)
    defer: list[DeferredNote] = Field(default_factory=list)
    decline: DeclineInfo | None = None
    emotion: EmotionPlan | None = None
    offer_live_agent: bool = False
    resume_anchor: str
    quick_replies: list[str] = Field(default_factory=list)
    max_sentences: int = 4
    fallback_reply: str
    grounding: GroundingRequest = Field(default_factory=GroundingRequest)


class ConsentObservation(BaseModel):
    """One poll of the policyholder's consent status (§11.3); `exhausted` means the sequence is used up."""

    status: str
    exhausted: bool = False


class SelectorChoice(BaseModel):
    """ClaimSelector output (§8.2.6). Untrusted: the policy checks it against the candidates."""

    case_id: str = ""
    path: Path | None = None


class Observations(BaseModel):
    """External status gathered by the orchestrator before decide(), so the policy stays pure (§11.3)."""

    consent: ConsentObservation | None = None
    claim_selection: SelectorChoice | None = None


@dataclass(frozen=True)
class PolicyDecision:
    """Result of PolicyEngine.decide(). `state` is a modified copy; the input state is never mutated.

    A dataclass rather than a Pydantic model so it can reference SessionState without an import cycle.
    """

    state: SessionState
    directive: TurnDirective
    actions: list[PlannedAction] = field(default_factory=list)
    events: list[Event] = field(default_factory=list)
