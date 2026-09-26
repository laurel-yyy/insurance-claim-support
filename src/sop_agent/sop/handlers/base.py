"""Shared types for phase handlers: turn context, directive parts and step results (SPEC §7.4).

Turn-level signals (yes/no, "done", "deny") belong to the phase the turn started in, and a yes/no only to the
phase that asked (DECISIONS D29). `TurnContext.answer()` enforces that and consumes the answer once.
"""

from dataclasses import dataclass, field
from typing import Any, Protocol

from sop_agent.data.repository import ClaimsRepository
from sop_agent.domain.enums import EscalationReason, Phase
from sop_agent.memory.state import SessionState
from sop_agent.nlu.schema import Confirmation, DialogAct, NLUResult
from sop_agent.sop.directive import (
    AskFor,
    DeferredNote,
    Event,
    EventType,
    GroundingRequest,
    Observations,
    PendingQuestion,
    PendingQuestionKind,
    PlannedAction,
)
from sop_agent.sop.emotion import EmotionConfig
from sop_agent.sop.scope import ScopeConfig
from sop_agent.sop.verification import VerifyConfig


@dataclass(frozen=True)
class PolicyConfig:
    verify: VerifyConfig = field(default_factory=VerifyConfig)
    scope: ScopeConfig = field(default_factory=ScopeConfig)
    emotion: EmotionConfig = field(default_factory=EmotionConfig)


@dataclass
class TurnContext:
    nlu: NLUResult
    observations: Observations
    repo: ClaimsRepository
    cfg: PolicyConfig
    turn: int
    origin_phase: Phase
    pending: PendingQuestion | None
    answer_consumed: bool = False

    def answer(self, kind: PendingQuestionKind, phase: Phase) -> Confirmation | None:
        """The caller's yes/no/unclear to `kind`, only for the phase that asked it, and only once."""
        pending = self.pending
        if (
            self.answer_consumed
            or pending is None
            or pending.kind is not kind
            or pending.asked_in_phase is not phase
            or phase is not self.origin_phase
            or self.nlu.confirmation is None
        ):
            return None
        self.answer_consumed = True
        return self.nlu.confirmation

    def signal(self, act: DialogAct, phase: Phase) -> bool:
        """A turn-level dialog act, visible only to the phase the turn started in."""
        return phase is self.origin_phase and act in self.nlu.dialog_acts


@dataclass
class Parts:
    """The handler's contribution to the TurnDirective."""

    must: list[str] = field(default_factory=list)
    must_not: list[str] = field(default_factory=list)
    ask_for: AskFor | None = None
    acknowledge: list[str] = field(default_factory=list)
    answer_now: list[str] = field(default_factory=list)
    defer: list[DeferredNote] = field(default_factory=list)
    quick_replies: list[str] = field(default_factory=list)
    offer_live_agent: bool = False
    resume_anchor: str = ""
    fallback_reply: str = ""
    max_sentences: int = 4
    grounding: GroundingRequest = field(default_factory=GroundingRequest)


@dataclass
class StepResult:
    parts: Parts
    next_phase: Phase | None = None
    escalation: EscalationReason | None = None
    actions: list[PlannedAction] = field(default_factory=list)


class Handler(Protocol):
    def step(self, state: SessionState, ctx: TurnContext, entering: bool) -> StepResult: ...


def emit(state: SessionState, ctx: TurnContext, kind: EventType, **data: Any) -> None:
    """Append an event to the session timeline for the phase the state is in now."""
    state.events.append(Event(type=kind, turn=ctx.turn, phase=state.phase, data=dict(data)))


def ask(
    state: SessionState,
    ctx: TurnContext,
    kind: PendingQuestionKind,
    *,
    on_yes: PlannedAction | None = None,
    **payload: Any,
) -> None:
    """Set the single open question; only the latest one counts (INV-6). Offers emit their event here."""
    state.pending_question = PendingQuestion(
        kind=kind, asked_in_phase=state.phase, asked_turn=ctx.turn, payload=dict(payload), on_yes=on_yes
    )
    offered = _OFFER_EVENTS.get(kind)
    if offered is not None:
        emit(state, ctx, offered)


_OFFER_EVENTS: dict[PendingQuestionKind, EventType] = {
    PendingQuestionKind.OFFER_LIVE_AGENT: EventType.LIVE_AGENT_OFFERED,
    PendingQuestionKind.OFFER_SUMMARY_EMAIL: EventType.EMAIL_OFFERED,
}


def escalate(reason: EscalationReason) -> StepResult:
    return StepResult(parts=Parts(), next_phase=Phase.ESCALATED, escalation=reason)
