"""VERIFY_ID, identity sub-stage (SPEC §8.1.2-§8.1.6). STRICT: code decides, the LLM only words it.

Replies never say which field failed, never confirm a policy exists and never read back stored values (V3, V9).
The authorization and consent sub-stages live in verify_rep.py.
"""

from sop_agent.domain.enums import (
    CallerRole,
    EscalationReason,
    IdentityField,
    IdentityStatus,
    Phase,
    VerifyStage,
)
from sop_agent.memory.state import SessionState
from sop_agent.sop import templates
from sop_agent.sop.directive import AskFor, EventType, PendingQuestionKind
from sop_agent.sop.handlers import verify_rep
from sop_agent.sop.handlers.base import Parts, StepResult, TurnContext, ask, escalate
from sop_agent.sop.verification import (
    Ambiguous,
    Failed,
    fields_still_needed,
    is_feasible,
    run_identity_check,
    suggest_fields,
)

MUST_NOT = [
    "Share, confirm or hint at any claim, policy or account information, including whether a record exists.",
    "Say which detail didn't match, or read back any stored detail.",
    "Repeat an ID number or a full date of birth.",
]
RESUME = "identity verification"


class VerifyIdHandler:
    def step(self, state: SessionState, ctx: TurnContext, entering: bool) -> StepResult:
        if state.verify_stage is not VerifyStage.IDENTITY:
            return verify_rep.step(state, ctx)
        check = run_identity_check(
            state.memory.identity,
            state.memory.representative.caller_role,
            ctx.repo,
            ctx.cfg.verify,
            turn=ctx.turn,
            phase=state.phase,
        )
        state.memory.identity = check.identity
        state.events.extend(check.events)
        status = check.identity.status
        if status is IdentityStatus.LOCKED:
            return escalate(EscalationReason.VERIFICATION_LOCKED)
        if status is IdentityStatus.VERIFIED:
            return StepResult(parts=_verified_parts(), next_phase=Phase.RESOLVE_INTENT)
        if status is IdentityStatus.IDENTITY_VERIFIED:
            state.verify_stage = VerifyStage.AUTHORIZATION
            return verify_rep.step(state, ctx)
        if isinstance(check.outcome, Failed):
            return StepResult(parts=_failed_parts(state))
        return StepResult(parts=_collect_parts(state, ctx, ambiguous=isinstance(check.outcome, Ambiguous)))


def _verified_parts() -> Parts:
    return Parts(
        must=["Confirm briefly that the caller is verified."],
        resume_anchor="verified",
        fallback_reply=templates.VERIFIED,
    )


def _failed_parts(state: SessionState) -> Parts:
    fields = suggest_fields(state.memory.identity)
    return Parts(
        must=[
            "Say the details couldn't be verified, in generic words.",
            "Ask the caller to double-check what they shared or provide another detail from the options.",
        ],
        must_not=list(MUST_NOT),
        ask_for=AskFor(fields=fields, count=1) if fields else None,
        resume_anchor=f"{RESUME}: double-check details or add another",
        fallback_reply=templates.verification_failed(fields),
    )


def _captured_labels(state: SessionState, ctx: TurnContext, kind: EventType) -> list[IdentityField]:
    return [
        IdentityField(e.data["field"])
        for e in state.events
        if e.turn == ctx.turn and e.type is kind and "field" in e.data
    ]


def _collect_parts(state: SessionState, ctx: TurnContext, *, ambiguous: bool) -> Parts:
    identity = state.memory.identity
    parts = Parts(must_not=list(MUST_NOT))
    parts.acknowledge += [
        f"Received {templates.FIELD_LABELS[f]}."
        for f in _captured_labels(state, ctx, EventType.IDENTITY_FIELD_CAPTURED)
    ]
    parts.acknowledge += [
        f"Noted what the caller needs: {m}."
        for m in state.memory.case_hints.raw_mentions[-1:]
        if _hint_this_turn(state, ctx)
    ]
    if state.memory.representative.caller_role is CallerRole.AUTHORIZED_REPRESENTATIVE:
        parts.must.append(
            "Make clear the identity details requested are the policyholder's, not the caller's."
        )
    invalid = _captured_labels(state, ctx, EventType.IDENTITY_FIELD_INVALID)
    for field in invalid:
        parts.must.append(
            f"Ask the caller to restate {templates.FIELD_LABELS[field]} as {templates.FIELD_FORMAT_HINTS[field]}."
        )
    if not is_feasible(identity, ctx.cfg.verify):
        parts.must.append("Explain that verification can't be completed with the details available and why.")
        parts.offer_live_agent = True
        ask(state, ctx, PendingQuestionKind.OFFER_LIVE_AGENT)
        parts.resume_anchor = f"{RESUME}: not enough details available"
        parts.fallback_reply = templates.VERIFICATION_INFEASIBLE
        return parts
    fields = suggest_fields(identity)
    needed = 1 if ambiguous else fields_still_needed(identity, ctx.cfg.verify)
    if needed == 0:
        needed = 1  # Nothing new since a failed evaluation: double-check or add another detail
        parts.must.append("Ask the caller to double-check what they shared or provide another detail.")
    parts.must.append(f"Ask for {needed} more identity detail(s), offering the listed options.")
    parts.ask_for = AskFor(fields=fields, count=needed)
    parts.resume_anchor = f"{RESUME}, {needed} more detail(s) needed"
    parts.fallback_reply = (
        templates.invalid_field(invalid[0]) if invalid else templates.ask_more(needed, fields)
    )
    parts.max_sentences = 5
    return parts


def _hint_this_turn(state: SessionState, ctx: TurnContext) -> bool:
    return any(e.type is EventType.HINT_STORED and e.turn == ctx.turn for e in state.events)
