"""PolicyEngine.decide(): the deterministic core of every turn (SPEC §3.1 step 4, §7.4).

Pure and synchronous: (state, nlu, observations) -> PolicyDecision. The input state is never mutated. LLM
output reaches here only as NLU fields; phase, verification and actions are decided by code (INV-1, INV-5).
"""

from sop_agent.data.repository import ClaimsRepository
from sop_agent.domain.enums import EscalationReason, Phase
from sop_agent.memory.merge import merge
from sop_agent.memory.state import SessionState
from sop_agent.nlu.schema import NLUResult
from sop_agent.sop import templates
from sop_agent.sop.directive import (
    ActionKind,
    ActionResult,
    Observations,
    PendingQuestionKind,
    PlannedAction,
    PolicyDecision,
    TurnDirective,
)
from sop_agent.sop.handlers.base import Handler, Parts, PolicyConfig, StepResult, TurnContext, ask
from sop_agent.sop.handlers.common import Decorations, apply_global_rules
from sop_agent.sop.handlers.post_process import PostProcessHandler, settle_send
from sop_agent.sop.handlers.process_case import ProcessCaseHandler
from sop_agent.sop.handlers.resolve_intent import ResolveIntentHandler
from sop_agent.sop.handlers.terminal import TerminalHandler, escalated_parts
from sop_agent.sop.handlers.verify_id import VerifyIdHandler
from sop_agent.sop.phases import PHASES, TERMINAL_PHASES
from sop_agent.sop.templates import QuickReply
from sop_agent.sop.transitions import HopCounter, transition

GLOBAL_MUST_NOT = [
    "Say that any action happened (request sent, email sent, transfer) unless it is in events."
]
EMOTION_EXTRA_SENTENCES = 1

HANDLERS: dict[Phase, Handler] = {
    Phase.VERIFY_ID: VerifyIdHandler(),
    Phase.RESOLVE_INTENT: ResolveIntentHandler(),
    Phase.PROCESS_CASE: ProcessCaseHandler(),
    Phase.POST_PROCESS: PostProcessHandler(),
    Phase.ESCALATED: TerminalHandler(),
    Phase.ENDED: TerminalHandler(),
}


class PolicyEngine:
    def __init__(self, repo: ClaimsRepository, config: PolicyConfig) -> None:
        self._repo = repo
        self._config = config

    @property
    def config(self) -> PolicyConfig:
        return self._config

    def decide(self, state: SessionState, nlu: NLUResult, observations: Observations) -> PolicyDecision:
        """Merge memory, apply global rules, run phase handlers (up to 3 hops) and build the directive."""
        new = state.model_copy(deep=True)
        new.counters.turn_index += 1
        new.counters.turns_in_phase += 1
        turn, start, origin = new.counters.turn_index, len(new.events), new.phase
        ctx = TurnContext(
            nlu=nlu,
            observations=observations,
            repo=self._repo,
            cfg=self._config,
            turn=turn,
            origin_phase=origin,
            pending=new.pending_question,
        )
        if origin in TERMINAL_PHASES:
            result = HANDLERS[origin].step(new, ctx, entering=False)
            return self._decision(new, result.parts, Decorations(), [], start)

        new, _ = merge(new, nlu, turn)
        new.pending_question = None  # Only a question asked this turn stays open (INV-6)
        outcome = apply_global_rules(new, ctx, start)
        if outcome.escalation is not None:
            new, parts, actions = self._escalate(new, outcome.escalation, HopCounter())
        else:
            new, parts, actions = self._run_handlers(new, ctx)
        if new.phase is not origin:
            new.counters.persuasion_attempts = 0
            new.counters.negative_emotion_streak = 0
        self._apply_offer(new, ctx, outcome.decorations, parts)
        return self._decision(new, parts, outcome.decorations, actions, start)

    def settle(self, state: SessionState, results: list[ActionResult]) -> PolicyDecision | None:
        """Finish a turn whose outcome depends on an executed action (D38). Pure, like decide().

        Only the summary email needs this: success ends the session, a failure stays in POST_PROCESS and
        offers a retry. Returns None when nothing needs settling, so the original directive stands.
        """
        sent = [r for r in results if r.action.kind is ActionKind.SEND_SUMMARY_EMAIL]
        if not sent or state.phase is not Phase.POST_PROCESS:
            return None
        new = state.model_copy(deep=True)
        turn = new.counters.turn_index
        start = next((i for i, e in enumerate(new.events) if e.turn == turn), len(new.events))
        ctx = TurnContext(
            nlu=NLUResult(),
            observations=Observations(),
            repo=self._repo,
            cfg=self._config,
            turn=turn,
            origin_phase=Phase.POST_PROCESS,
            pending=None,
        )
        new.pending_question = None
        result = settle_send(new, ctx, sent[-1].action, sent[-1].ok)
        parts = result.parts
        if result.next_phase is not None:
            new = transition(new, result.next_phase)
            parts = HANDLERS[new.phase].step(new, ctx, entering=True).parts
        return self._decision(new, parts, Decorations(), [], start)

    def _run_handlers(
        self, state: SessionState, ctx: TurnContext
    ) -> tuple[SessionState, Parts, list[PlannedAction]]:
        hops = HopCounter()
        actions: list[PlannedAction] = []
        entering = False
        while True:
            result: StepResult = HANDLERS[state.phase].step(state, ctx, entering)
            actions += result.actions
            if result.next_phase is None:
                return state, result.parts, actions
            if result.next_phase is Phase.ESCALATED:
                state, parts, transfer = self._escalate(
                    state, result.escalation or EscalationReason.CALLER_REQUEST, hops
                )
                return state, parts, actions + transfer
            state = hops.hop(state, result.next_phase)
            entering = True

    def _escalate(
        self, state: SessionState, reason: EscalationReason, hops: HopCounter
    ) -> tuple[SessionState, Parts, list[PlannedAction]]:
        state = hops.hop(state, Phase.ESCALATED) if hops.can_hop() else transition(state, Phase.ESCALATED)
        action = PlannedAction(kind=ActionKind.TRANSFER_TO_LIVE_AGENT, params={"reason": reason.value})
        return state, escalated_parts(state, reason), [action]

    def _apply_offer(self, state: SessionState, ctx: TurnContext, deco: Decorations, parts: Parts) -> None:
        """A live-agent offer from the global rules (off-topic, persuasion, emotion) wins the open question."""
        if deco.offer_reason is None or state.phase in TERMINAL_PHASES:
            return
        ask(state, ctx, PendingQuestionKind.OFFER_LIVE_AGENT, reason=deco.offer_reason.value)
        parts.offer_live_agent = True
        parts.quick_replies = [QuickReply.LIVE_AGENT.value, QuickReply.CONTINUE_HERE.value]
        parts.must.append("Offer a transfer to a member of our claims team.")

    def _decision(
        self, state: SessionState, parts: Parts, deco: Decorations, actions: list[PlannedAction], start: int
    ) -> PolicyDecision:
        events = list(state.events[start:])
        terminal = state.phase in TERMINAL_PHASES
        directive = TurnDirective(
            phase=state.phase,
            verify_stage=state.verify_stage if state.phase is Phase.VERIFY_ID else None,
            freedom=PHASES[state.phase].freedom,
            events=events,
            must=([] if terminal else deco.must) + parts.must,
            must_not=_unique(parts.must_not + GLOBAL_MUST_NOT),
            ask_for=parts.ask_for,
            acknowledge=_unique(deco.acknowledge + parts.acknowledge),
            answer_now=_unique(([] if terminal else deco.answer_now) + parts.answer_now),
            defer=deco.defer + parts.defer,
            decline=None if terminal else deco.decline,
            emotion=deco.emotion,
            offer_live_agent=parts.offer_live_agent,
            resume_anchor=parts.resume_anchor or PHASES[state.phase].goal,
            quick_replies=parts.quick_replies,
            max_sentences=parts.max_sentences + (EMOTION_EXTRA_SENTENCES if deco.emotion else 0),
            fallback_reply=_fallback(parts, deco, terminal),
            grounding=parts.grounding,
        )
        return PolicyDecision(state=state, directive=directive, actions=actions, events=events)


def _unique(items: list[str]) -> list[str]:
    return list(dict.fromkeys(items))


def _fallback(parts: Parts, deco: Decorations, terminal: bool) -> str:
    """Deterministic reply that leaks nothing (INV-8): decline, boundary, the step's template, offer."""
    pieces = []
    if not terminal and deco.abuse_boundary:
        pieces.append(templates.ABUSE_BOUNDARY)
    if not terminal and deco.decline is not None:
        pieces.append(templates.DECLINE)
    pieces.append(parts.fallback_reply or templates.ASK_NEED)
    if (
        parts.offer_live_agent
        and templates.LIVE_AGENT_OFFER not in pieces[-1]
        and "transfer" not in pieces[-1]
    ):
        pieces.append(templates.LIVE_AGENT_OFFER)
    return " ".join(pieces)
