"""PROCESS_CASE (SPEC §8.3). GUIDED: the LLM reasons over grounding and read-only tools.

Code still decides the path's guidance, which topics to ground, follow-up items, when alternatives are
exhausted, claim rejection and switching, and completion.
"""

from sop_agent.domain.enums import PATHS_NEEDING_CLAIM, EscalationReason, Path, Phase
from sop_agent.domain.models import Claim
from sop_agent.memory.state import SessionState
from sop_agent.nlu.schema import Confirmation, DialogAct
from sop_agent.sop import templates
from sop_agent.sop.directive import EventType, GroundingRequest, PendingQuestionKind
from sop_agent.sop.guidance import match_claim_document, normalize_words, select_topics
from sop_agent.sop.handlers.base import Parts, StepResult, TurnContext, ask, emit
from sop_agent.sop.playbooks import plan_for
from sop_agent.sop.resolver import PATH_MIN_CONFIDENCE, ResolutionMode, contradicts
from sop_agent.sop.templates import QuickReply

TOPIC_INTENT_MIN_CONFIDENCE = 0.5
APPEAL_WORD = "appeal"


class ProcessCaseHandler:
    def step(self, state: SessionState, ctx: TurnContext, entering: bool) -> StepResult:
        claim = _selected_claim(state, ctx)
        if claim is None:
            _clear_selection(state)
            return StepResult(parts=Parts(), next_phase=Phase.RESOLVE_INTENT)
        if not entering:
            moved = _caller_moves_on(state, ctx, claim)
            if moved is not None:
                return moved
        return StepResult(parts=_work_the_case(state, ctx, claim, entering))


def _selected_claim(state: SessionState, ctx: TurnContext) -> Claim | None:
    case_id = state.memory.selected_case_id
    claim = ctx.repo.claim(case_id) if case_id else None
    if claim is None or claim.party_id != state.memory.identity.party_id:
        return None
    return claim


def _clear_selection(state: SessionState) -> None:
    state.memory.selected_case_id = None
    state.memory.selected_path = None


def _auto_resolved(state: SessionState, case_id: str) -> bool:
    resolved = [e for e in state.events if e.type is EventType.CLAIM_RESOLVED]
    return (
        bool(resolved)
        and resolved[-1].data.get("case_id") == case_id
        and (resolved[-1].data.get("mode") == ResolutionMode.AUTO.value)
    )


def _caller_moves_on(state: SessionState, ctx: TurnContext, claim: Claim) -> StepResult | None:
    """Rejection, a switch to another claim, or being done (§8.2.5, §8.3.8)."""
    nlu = ctx.nlu
    first_reply = state.counters.turns_in_phase == 1 and ctx.pending is None
    if (
        first_reply
        and ctx.signal(DialogAct.DENY, Phase.PROCESS_CASE)
        and not ctx.signal(DialogAct.DONE, Phase.PROCESS_CASE)  # "No, that's all" closes; it doesn't reject
        and _auto_resolved(state, claim.case_id)
    ):
        state.memory.excluded_case_ids.add(claim.case_id)
        emit(state, ctx, EventType.CLAIM_REJECTED_BY_CALLER, case_id=claim.case_id)
        _clear_selection(state)
        return StepResult(parts=Parts(), next_phase=Phase.RESOLVE_INTENT)
    if contradicts(claim, nlu.case_id, nlu.case_type, nlu.claim_status):
        _clear_selection(state)
        return StepResult(parts=Parts(), next_phase=Phase.RESOLVE_INTENT)
    anything_else = ctx.answer(PendingQuestionKind.ANYTHING_ELSE, Phase.PROCESS_CASE)
    if anything_else is Confirmation.NO or ctx.signal(DialogAct.DONE, Phase.PROCESS_CASE):
        return StepResult(parts=Parts(), next_phase=Phase.POST_PROCESS)
    _maybe_switch_path(state, ctx)
    return None


def _maybe_switch_path(state: SessionState, ctx: TurnContext) -> None:
    claim_intents = [
        i for i in ctx.nlu.intents if i.path in PATHS_NEEDING_CLAIM and i.confidence >= PATH_MIN_CONFIDENCE
    ]
    if not claim_intents:
        return
    best = max(claim_intents, key=lambda i: i.confidence).path
    if best is not state.memory.selected_path:
        state.memory.selected_path = best
        emit(state, ctx, EventType.PATH_SELECTED, path=best.value)


def _work_the_case(state: SessionState, ctx: TurnContext, claim: Claim, entering: bool) -> Parts:
    memory, nlu = state.memory, ctx.nlu
    path = memory.selected_path or Path.STATUS_INQUIRY
    guideline = ctx.repo.document_guideline()
    today = state.session_settings.today
    wants_appeal = APPEAL_WORD in normalize_words(nlu.text).split()
    plan = plan_for(path, claim, guideline, today, wants_appeal)
    known = {f.item for f in memory.case_log.follow_ups}
    memory.case_log.follow_ups += [f for f in plan.follow_ups if f.item not in known]

    parts = Parts(resume_anchor=f"claim {claim.case_id}, {path.value}", max_sentences=6)
    parts.fallback_reply = templates.process_case(claim.case_id)
    auto_entry = entering and _auto_resolved(state, claim.case_id)
    if auto_entry:
        parts.must.append("Name the claim (claim number, type, creation date) so the caller can correct it.")
    parts.must += plan.must
    parts.answer_now += [f"Asked earlier: {q.text}" for q in memory.deferred_questions if not q.answered]
    parts.grounding = _grounding(state, ctx, claim, path, entering)
    offer = plan.offer_live_agent or _alternatives(state, ctx, claim, parts)
    if offer:
        parts.offer_live_agent = True
        parts.quick_replies = [QuickReply.LIVE_AGENT.value, QuickReply.CONTINUE_HERE.value]
        reason = _offer_reason(state, ctx)
        ask(state, ctx, PendingQuestionKind.OFFER_LIVE_AGENT, reason=reason.value)
    elif not auto_entry:
        parts.must.append("End by asking whether there's anything else you can help with.")
        ask(state, ctx, PendingQuestionKind.ANYTHING_ELSE)
    return parts


def _grounding(
    state: SessionState, ctx: TurnContext, claim: Claim, path: Path, entering: bool
) -> GroundingRequest:
    nlu = ctx.nlu
    intents = {path} | {i.path for i in nlu.intents if i.confidence >= TOPIC_INTENT_MIN_CONFIDENCE}
    asked = bool(nlu.questions) or DialogAct.ASK_QUESTION in nlu.dialog_acts
    selection = select_topics(
        ctx.repo.document_guideline(),
        claim,
        intents,
        nlu.followup_topics,
        nlu.text,
        followup_asked=asked and not entering,
    )
    return GroundingRequest(
        topics=[t.topic for t in selection.triggered],
        background_topics=[t.topic for t in selection.background],
        use_followup_fallback=selection.fallback is not None,
        skipped_topics=list(selection.skipped),
    )


def _alternatives(state: SessionState, ctx: TurnContext, claim: Claim, parts: Parts) -> bool:
    """§8.3.7: share alternatives the first time; offer a specialist once they're exhausted."""
    log = state.memory.case_log
    newly_shared: list[str] = []
    for mention in ctx.nlu.unavailable_documents:
        document = match_claim_document(mention, claim)
        if document is None:
            continue
        if document not in log.alternatives_shared_for:
            log.alternatives_shared_for.add(document)
            newly_shared.append(document)
    if newly_shared:
        parts.grounding.alternatives_for = newly_shared
        parts.must.append("Give the alternatives from GROUNDING for: " + ", ".join(newly_shared) + ".")
        return False
    if ctx.nlu.no_substitutes_available and log.alternatives_shared_for:
        parts.must.append(
            "Say that since no substitute is available, a claims specialist should review the file for manual "
            "options, and offer that transfer."
        )
        return True
    return False


def _offer_reason(state: SessionState, ctx: TurnContext) -> EscalationReason:
    if ctx.nlu.no_substitutes_available and state.memory.case_log.alternatives_shared_for:
        return EscalationReason.DOCUMENT_ALTERNATIVES_EXHAUSTED
    return EscalationReason.CALLER_REQUEST
