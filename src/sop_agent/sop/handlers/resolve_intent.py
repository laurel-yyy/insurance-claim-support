"""RESOLVE_INTENT (SPEC §8.2). BOUNDED: code scores and offers candidates; the LLM may only pick among them.

A ClaimSelector choice arrives as an observation and is accepted only if it names one of the candidates.
"""

from collections.abc import Sequence

from sop_agent.domain.enums import PATHS_NEEDING_CLAIM, Path, Phase
from sop_agent.domain.models import Claim
from sop_agent.memory.state import SessionState
from sop_agent.nlu.schema import Confirmation, DialogAct
from sop_agent.sop import templates
from sop_agent.sop.directive import EventType, PendingQuestionKind
from sop_agent.sop.handlers.base import Parts, StepResult, TurnContext, ask, emit
from sop_agent.sop.resolver import Ambiguous, NoMatch, ResolutionMode, Unique, infer_path, resolve
from sop_agent.sop.templates import QuickReply


class ResolveIntentHandler:
    def step(self, state: SessionState, ctx: TurnContext, entering: bool) -> StepResult:
        if _caller_is_done(state, ctx):
            return StepResult(parts=Parts(), next_phase=Phase.POST_PROCESS)
        party_id = state.memory.identity.party_id or ""
        claims = list(ctx.repo.claims_for(party_id))
        if not claims:
            return StepResult(parts=_no_claims(state, ctx))
        chosen = _selector_choice(ctx, claims)
        if chosen is not None:
            return _select(state, ctx, chosen[0], mode=ResolutionMode.CHOSEN, path=chosen[1])
        memory = state.memory
        result = resolve(claims, memory.case_hints, memory.excluded_case_ids, state.session_settings.today)
        if isinstance(result, Unique):
            mode = ResolutionMode.CHOSEN if _choosing(ctx) else ResolutionMode.AUTO
            return _select(state, ctx, result.claim, mode=mode, path=None)
        if isinstance(result, Ambiguous):
            return StepResult(parts=_choose(state, ctx, result.candidates))
        if isinstance(result, NoMatch):
            return StepResult(parts=_choose(state, ctx, result.recent, no_match=True))
        return StepResult(parts=_all_rejected(state, ctx))


def _caller_is_done(state: SessionState, ctx: TurnContext) -> bool:
    if ctx.answer(PendingQuestionKind.ANYTHING_ELSE, Phase.RESOLVE_INTENT) is Confirmation.NO:
        return True
    return ctx.signal(DialogAct.DONE, Phase.RESOLVE_INTENT)


def _choosing(ctx: TurnContext) -> bool:
    pending = ctx.pending
    return (
        pending is not None
        and pending.kind is PendingQuestionKind.CHOOSE_CLAIM
        and pending.asked_in_phase is Phase.RESOLVE_INTENT
        and ctx.origin_phase is Phase.RESOLVE_INTENT
    )


def _selector_choice(ctx: TurnContext, claims: Sequence[Claim]) -> tuple[Claim, Path | None] | None:
    """A ClaimSelector answer to CHOOSE_CLAIM, accepted only if it names a listed candidate (§8.2.6)."""
    selection = ctx.observations.claim_selection
    if not _choosing(ctx) or selection is None or ctx.pending is None:
        return None
    candidate_ids = {str(c) for c in ctx.pending.payload.get("candidates", [])}
    if selection.case_id not in candidate_ids:
        return None
    claim = next((c for c in claims if c.case_id == selection.case_id), None)
    if claim is None:
        return None
    return claim, selection.path if selection.path in PATHS_NEEDING_CLAIM else None


def _select(
    state: SessionState, ctx: TurnContext, claim: Claim, *, mode: ResolutionMode, path: Path | None
) -> StepResult:
    memory = state.memory
    chosen_path = path or infer_path(memory.intent_candidates.values(), claim)
    memory.selected_case_id, memory.selected_path = claim.case_id, chosen_path
    if claim.case_id not in memory.case_log.discussed_case_ids:
        memory.case_log.discussed_case_ids.append(claim.case_id)
    emit(state, ctx, EventType.CLAIM_RESOLVED, case_id=claim.case_id, mode=mode.value)
    emit(state, ctx, EventType.PATH_SELECTED, path=chosen_path.value)
    return StepResult(parts=Parts(resume_anchor=f"claim {claim.case_id}"), next_phase=Phase.PROCESS_CASE)


def _choose(
    state: SessionState, ctx: TurnContext, claims: Sequence[Claim], *, no_match: bool = False
) -> Parts:
    ids = [c.case_id for c in claims]
    ask(state, ctx, PendingQuestionKind.CHOOSE_CLAIM, candidates=ids)
    emit(state, ctx, EventType.CLAIM_CANDIDATES, case_ids=ids, no_match=no_match)
    lead = (
        "Say you couldn't tell which claim they mean, and list their most recent claims."
        if no_match
        else "Say a few claims could match."
    )
    return Parts(
        must=[
            lead,
            "Describe each candidate in a short numbered list: claim number, type, creation date, status.",
            "Ask which one they mean.",
        ],
        quick_replies=ids,
        resume_anchor="choose which claim to discuss",
        fallback_reply=templates.choose_claim(ids),
        max_sentences=6,
    )


def _no_claims(state: SessionState, ctx: TurnContext) -> Parts:
    if not any(e.type is EventType.NO_CLAIMS_ON_FILE for e in state.events):
        emit(state, ctx, EventType.NO_CLAIMS_ON_FILE)
    ask(state, ctx, PendingQuestionKind.OFFER_LIVE_AGENT)
    return Parts(
        must=[
            "Say plainly there are no claims on file for this account; don't guess at reasons.",
            "Say general questions can still be answered, and offer a transfer (for example, to start a claim).",
        ],
        quick_replies=[QuickReply.LIVE_AGENT.value, QuickReply.CONTINUE_HERE.value],
        offer_live_agent=True,
        resume_anchor="no claims on file",
        fallback_reply=templates.NO_CLAIMS,
    )


def _all_rejected(state: SessionState, ctx: TurnContext) -> Parts:
    ask(state, ctx, PendingQuestionKind.OFFER_LIVE_AGENT)
    return Parts(
        must=[
            "Say none of the claims on file seem to be the one they mean.",
            "Ask for more details about the claim, or offer a transfer to a member of our claims team.",
        ],
        quick_replies=[QuickReply.LIVE_AGENT.value, QuickReply.CONTINUE_HERE.value],
        offer_live_agent=True,
        resume_anchor="identify the claim",
        fallback_reply=templates.LIVE_AGENT_OFFER,
    )
