"""Orchestrator: wires one turn together (SPEC §3.1, §11.4).

extract -> observe -> decide (pure) -> execute -> settle -> context -> respond -> guard -> persist + trace.
Any responder or guard failure gives the directive's deterministic fallback (INV-8); a failed action swaps in its
failure template and never claims success.
"""

import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field

from sop_agent.agent.context import ContextBuilder, Grounding
from sop_agent.agent.guard import GuardContext, OutputGuard, Violation
from sop_agent.agent.observer import Observer
from sop_agent.agent.responder import Responder, ResponderResult
from sop_agent.domain.clock import Clock
from sop_agent.domain.enums import IdentityStatus, Phase, VerifyStage
from sop_agent.memory.merge import mark_deferred_answered
from sop_agent.memory.state import ChatRole, ChatTurn, SessionSettings, SessionState
from sop_agent.memory.store import SessionStore
from sop_agent.nlu.extractor import Extractor
from sop_agent.nlu.schema import NLUResult, QuestionKind
from sop_agent.nlu.selector import ClaimSelector
from sop_agent.observability.logging import get_logger
from sop_agent.observability.trace import TraceSink, TurnTrace
from sop_agent.postprocess.writer import SummaryWriter
from sop_agent.sop import templates
from sop_agent.sop.directive import ActionKind, ActionResult, Event, EventType, Observations, TurnDirective
from sop_agent.sop.phases import TERMINAL_PHASES
from sop_agent.sop.policy import PolicyEngine
from sop_agent.tools.executor import ActionExecutor

RESPONDER_COMPONENT = "responder"
GREETING = (
    "Hi, I'm {agent_name} from {company_name} claims support. I can help with claim status, denials, and the "
    "documents a claim needs. Before I look at any account details, I'll need to verify your identity. What's your "
    "full name, and what can I help you with today?"
)

_log = get_logger(__name__)


@dataclass(frozen=True)
class Agents:
    """LLM-backed components for one session (a session may bring its own API key, M6)."""

    extractor: Extractor
    selector: ClaimSelector
    responder: Responder
    summary: SummaryWriter


@dataclass
class TurnResult:
    session_id: str
    reply: str
    phase: Phase
    verify_stage: VerifyStage | None
    quick_replies: list[str] = field(default_factory=list)
    events: list[Event] = field(default_factory=list)
    phase_trail: list[Phase] = field(default_factory=list)
    fallback_used: bool = False

    @property
    def ended(self) -> bool:
        return self.phase is Phase.ENDED

    @property
    def escalated(self) -> bool:
        return self.phase is Phase.ESCALATED


@dataclass
class _Reply:
    text: str
    fallback_used: bool
    violations: list[Violation] = field(default_factory=list)


class Orchestrator:
    def __init__(
        self,
        *,
        store: SessionStore,
        policy: PolicyEngine,
        context: ContextBuilder,
        guard: OutputGuard,
        executor: ActionExecutor,
        observer: Observer,
        agents_for: Callable[[str], Agents],
        tracer: TraceSink,
        clock: Clock,
        agent_name: str,
        company_name: str,
        default_consent_scenario: str,
    ) -> None:
        self._store, self._policy, self._context, self._guard = store, policy, context, guard
        self._executor, self._observer, self._agents_for, self._tracer = (
            executor,
            observer,
            agents_for,
            tracer,
        )
        self._clock = clock
        self._greeting = GREETING.format(agent_name=agent_name, company_name=company_name)
        self._default_scenario = default_consent_scenario

    def start_session(self, consent_scenario: str | None = None) -> TurnResult:
        """Deterministic greeting; no LLM call (§11.4)."""
        scenario = consent_scenario or self._default_scenario
        state = SessionState(
            session_id=uuid.uuid4().hex,
            session_settings=SessionSettings(consent_scenario=scenario, today=self._clock.today()),
            created_at=self._clock.now(),
        )
        state.memory.representative.consent_scenario = scenario
        state.history.append(ChatTurn(role=ChatRole.AGENT, text=self._greeting, turn=0))
        self._store.put(state)
        return TurnResult(
            session_id=state.session_id,
            reply=self._greeting,
            phase=state.phase,
            verify_stage=state.verify_stage,
        )

    async def handle_turn(self, session_id: str, text: str) -> TurnResult:
        async with self._store.lock(session_id):
            started = time.perf_counter()
            state = self._store.get(session_id)
            phase_before = state.phase
            turn = state.counters.turn_index + 1
            state.history.append(ChatTurn(role=ChatRole.CALLER, text=text, turn=turn))
            agents = self._agents_for(session_id)
            if phase_before in TERMINAL_PHASES:
                nlu, observations = NLUResult(text=text), Observations()
            else:
                nlu = await agents.extractor.extract(text, state)
                observations = await self._observer.observe(state, text, agents.selector)
            decision = self._policy.decide(state, nlu, observations)
            state, results = self._executor.run(decision.actions, decision.state)
            state, directive = self._settle(state, decision.directive, results)
            state = await self._ensure_draft(state, agents.summary)
            directive = directive.model_copy(update={"events": [e for e in state.events if e.turn == turn]})
            grounding = self._context.build(state, directive)
            if phase_before in TERMINAL_PHASES:  # Fixed copy; no LLM call (§8.5)
                reply = _Reply(directive.fallback_reply, fallback_used=False)
            else:
                reply = await self._reply(state, directive, grounding, agents.responder)
            state = self._finish(state, directive, reply, turn, nlu)
            self._store.put(state)
            self._trace(state, phase_before, nlu, observations, directive, results, reply, started)
            return self._result(state, directive, reply, turn)

    def _settle(
        self, state: SessionState, directive: TurnDirective, results: list[ActionResult]
    ) -> tuple[SessionState, TurnDirective]:
        """Email outcomes via PolicyEngine.settle (D38); other failures get their failure template."""
        settled = self._policy.settle(state, results)
        if settled is not None:
            state, directive = settled.state, settled.directive
        failed = [r for r in results if not r.ok and r.action.kind is not ActionKind.SEND_SUMMARY_EMAIL]
        updates: dict[str, object] = {}
        if failed:
            notes = " ".join(templates.ACTION_FAILURE_TEMPLATES[r.action.kind] for r in failed)
            updates["fallback_reply"] = f"{notes} {directive.fallback_reply}"
            updates["must"] = [f"Say plainly: {notes}", *directive.must]
        return state, directive.model_copy(update=updates)

    async def _ensure_draft(self, state: SessionState, writer: SummaryWriter) -> SessionState:
        """C1: entering POST_PROCESS drafts the summary once, so the offer and C5 can use it (D52)."""
        if state.phase is not Phase.POST_PROCESS or state.email_draft is not None:
            return state
        pending = state.pending_question
        target = str(pending.payload.get("target", "")) if pending else ""
        state.email_draft = await writer.draft(state, target)
        _append(state, EventType.SUMMARY_DRAFTED, generated_by=state.email_draft.generated_by.value)
        return state

    async def _reply(
        self, state: SessionState, directive: TurnDirective, grounding: Grounding, responder: Responder
    ) -> _Reply:
        """Respond, guard, regenerate once on a violation, else the fallback. Nothing gets through unchecked."""
        try:
            draft = await responder.respond(state, directive, grounding)
            violations = self._guard.check(
                draft.text, self._guard_context(state, directive, grounding, draft.tool_results_text)
            )
            self._record_tools(state, draft)
            if violations:
                rules = sorted({v.rule.value for v in violations})
                draft = await responder.respond(state, directive, grounding, guard_notes=rules)
                self._record_tools(state, draft)
                second = self._guard.check(
                    draft.text, self._guard_context(state, directive, grounding, draft.tool_results_text)
                )
                if second:
                    return _Reply(directive.fallback_reply, True, violations + second)
            if not draft.text:
                return _Reply(directive.fallback_reply, True, violations)
            return _Reply(draft.text, False, violations)
        except Exception:  # noqa: BLE001 - INV-8: any responder or guard failure must fail closed
            _log.exception("responder failed; using fallback")
            return _Reply(directive.fallback_reply, True)

    def _guard_context(
        self, state: SessionState, directive: TurnDirective, grounding: Grounding, tool_text: str
    ) -> GuardContext:
        return GuardContext(
            verified=state.memory.identity.status is IdentityStatus.VERIFIED,
            party_id=state.memory.identity.party_id,
            phase=state.phase,
            caller_texts=[t.text for t in state.history if t.role is ChatRole.CALLER],
            caller_identity=state.memory.identity.valid_values(),
            grounding_text=grounding.as_json(),
            tool_text=tool_text,
            event_types=frozenset(e.type for e in directive.events),
        )

    def _record_tools(self, state: SessionState, draft: ResponderResult) -> None:
        for call in draft.tool_calls:
            kind = EventType.TOOL_DENIED if call.denied else EventType.TOOL_CALLED
            _append(state, kind, tool=call.name, ok=call.ok)
            for case_id in call.case_ids:
                if case_id not in state.memory.case_log.discussed_case_ids:
                    state.memory.case_log.discussed_case_ids.append(case_id)

    def _finish(
        self, state: SessionState, directive: TurnDirective, reply: _Reply, turn: int, nlu: NLUResult
    ) -> SessionState:
        for violation in reply.violations:
            _append(state, EventType.GUARD_BLOCKED, rule=violation.rule.value, token_kind=violation.kind)
        if reply.fallback_used:
            _append(state, EventType.LLM_FALLBACK, component=RESPONDER_COMPONENT)
        else:
            if state.phase is Phase.PROCESS_CASE and directive.answer_now:
                state = mark_deferred_answered(_record_deferred(state))
            state = _record_questions(state, nlu)
        state.history.append(ChatTurn(role=ChatRole.AGENT, text=reply.text, turn=turn))
        return state

    def _trace(
        self,
        state: SessionState,
        phase_before: Phase,
        nlu: NLUResult,
        observations: Observations,
        directive: TurnDirective,
        results: list[ActionResult],
        reply: _Reply,
        started: float,
    ) -> None:
        turn = state.counters.turn_index
        self._tracer.record(
            TurnTrace(
                session_id=state.session_id,
                turn=turn,
                phase_before=phase_before.value,
                phase_after=state.phase.value,
                verify_stage=state.verify_stage.value,
                nlu=nlu.model_dump(mode="json"),
                observations=observations.model_dump(mode="json"),
                events=[e.model_dump(mode="json") for e in state.events if e.turn == turn],
                directive=directive.model_dump(mode="json"),
                actions=[{"kind": r.action.kind.value, "ok": r.ok} for r in results],
                guard=[{"rule": v.rule.value, "kind": v.kind} for v in reply.violations],
                fallback_used=reply.fallback_used,
                latency_ms=int((time.perf_counter() - started) * 1000),
            )
        )

    def _result(self, state: SessionState, directive: TurnDirective, reply: _Reply, turn: int) -> TurnResult:
        events = [e for e in state.events if e.turn == turn]
        trail = [Phase(str(e.data["to"])) for e in events if e.type is EventType.PHASE_CHANGED]
        return TurnResult(
            session_id=state.session_id,
            reply=reply.text,
            phase=state.phase,
            verify_stage=state.verify_stage if state.phase is Phase.VERIFY_ID else None,
            quick_replies=list(directive.quick_replies),
            events=events,
            phase_trail=trail,
            fallback_used=reply.fallback_used,
        )


def _record_questions(state: SessionState, nlu: NLUResult) -> SessionState:
    """What was discussed (§8.4.1): the caller's questions answered after verification (D54)."""
    if state.memory.identity.status is not IdentityStatus.VERIFIED:
        return state
    log = state.memory.case_log
    for question in nlu.questions:
        text = question.text.strip()
        if text and question.kind is not QuestionKind.OUT_OF_SCOPE and text not in log.questions_answered:
            log.questions_answered.append(text)
    return state


def _record_deferred(state: SessionState) -> SessionState:
    """Deferred questions answered this turn also count as discussed, once."""
    log = state.memory.case_log
    for question in state.memory.deferred_questions:
        if not question.answered and question.text not in log.questions_answered:
            log.questions_answered.append(question.text)
    return state


def _append(state: SessionState, kind: EventType, **data: object) -> None:
    state.events.append(Event(type=kind, turn=state.counters.turn_index, phase=state.phase, data=dict(data)))
