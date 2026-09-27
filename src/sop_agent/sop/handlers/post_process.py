"""POST_PROCESS: the summary email consent gate (C1-C8, INV-6).

Only an explicit yes to the latest offer sends; the default is not to send. The target address lives in the
pending question's payload (state), never in the directive, and the LLM can't trigger a send.
"""

from enum import StrEnum

from sop_agent.domain.enums import PATHS_NEEDING_CLAIM, CallerRole, Phase
from sop_agent.domain.normalize import normalize_email
from sop_agent.memory.state import SessionState
from sop_agent.nlu.schema import Confirmation, DialogAct, QuestionKind
from sop_agent.sop import templates
from sop_agent.sop.directive import ActionKind, EventType, PendingQuestion, PendingQuestionKind, PlannedAction
from sop_agent.sop.handlers.base import Parts, StepResult, TurnContext, ask, emit
from sop_agent.sop.resolver import PATH_MIN_CONFIDENCE
from sop_agent.sop.templates import QuickReply

VAGUE_LIMIT = 2
SEND_FAILURE_LIMIT = 2
EMAIL_KINDS = (PendingQuestionKind.OFFER_SUMMARY_EMAIL, PendingQuestionKind.CONFIRM_ALT_EMAIL)
MUST_NOT = ["Say the email was sent unless EMAIL_SENT is in events."]


class SkipReason(StrEnum):
    """Why the summary email was skipped (C3, C6)."""

    DECLINED = "declined"
    VAGUE = "vague"
    SEND_FAILED = "send_failed"


class PostProcessHandler:
    def step(self, state: SessionState, ctx: TurnContext, entering: bool) -> StepResult:
        if entering or ctx.origin_phase is not Phase.POST_PROCESS:
            return StepResult(parts=_offer(state, ctx))
        if _new_account_need(ctx):
            state.email_draft = None  # C7: a later return to POST_PROCESS drafts and asks again
            return StepResult(parts=Parts(), next_phase=Phase.RESOLVE_INTENT)
        if ctx.nlu.summary_email and ctx.nlu.summary_email.strip():
            return StepResult(parts=_alternate_address(state, ctx, ctx.nlu.summary_email))
        pending = ctx.pending if ctx.pending and ctx.pending.kind in EMAIL_KINDS else None
        if pending is None:
            return StepResult(parts=_offer(state, ctx))
        return _answer(state, ctx, pending)


def _is_representative(state: SessionState) -> bool:
    return state.memory.representative.caller_role is CallerRole.AUTHORIZED_REPRESENTATIVE


def _file_address(state: SessionState, ctx: TurnContext) -> str:
    holder = ctx.repo.policyholder(state.memory.identity.party_id or "")
    return holder.email if holder else ""


def _ask_send(
    state: SessionState,
    ctx: TurnContext,
    kind: PendingQuestionKind,
    to: str,
    unclear: int = 0,
    failures: int = 0,
) -> None:
    action = PlannedAction(
        kind=ActionKind.SEND_SUMMARY_EMAIL,
        params={"to": to, "offer_kind": kind.value, "send_failures": failures},
    )
    ask(state, ctx, kind, on_yes=action, target=to)
    if state.pending_question is not None:
        state.pending_question.unclear_replies = unclear


def _offer(
    state: SessionState, ctx: TurnContext, unclear: int = 0, extra_must: list[str] | None = None
) -> Parts:
    """C1: offer the summary to the address on file (masked in GROUNDING)."""
    state.memory.summary_email_override = None
    _ask_send(state, ctx, PendingQuestionKind.OFFER_SUMMARY_EMAIL, _file_address(state, ctx), unclear)
    replies = [QuickReply.SEND_SUMMARY.value, QuickReply.NO_THANKS.value]
    if not _is_representative(state):
        replies.append(QuickReply.DIFFERENT_EMAIL.value)
    alt = "" if _is_representative(state) else ", or use a different address"
    return Parts(
        must=[
            *(extra_must or []),
            f"Offer to email a summary to the masked address in GROUNDING; they can send it, skip it{alt}.",
        ],
        must_not=list(MUST_NOT),
        quick_replies=replies,
        resume_anchor="offer the summary email",
        fallback_reply=templates.EMAIL_OFFER,
    )


def _new_account_need(ctx: TurnContext) -> bool:
    nlu = ctx.nlu
    if any(q.kind is QuestionKind.ACCOUNT for q in nlu.questions):
        return True
    if any(i.path in PATHS_NEEDING_CLAIM and i.confidence >= PATH_MIN_CONFIDENCE for i in nlu.intents):
        return True
    return DialogAct.STATE_NEED in nlu.dialog_acts


def _alternate_address(state: SessionState, ctx: TurnContext, raw: str) -> Parts:
    """C4: policyholders may switch addresses with a second confirmation; representatives may not (A8)."""
    if _is_representative(state):
        return _offer(
            state,
            ctx,
            extra_must=["Explain the summary can only go to the email on the policyholder's file."],
        )
    address = normalize_email(raw)
    if address is None:
        parts = _offer(
            state, ctx, extra_must=["Say that address looks incomplete and ask for the full address."]
        )
        parts.fallback_reply = templates.EMAIL_INVALID
        return parts
    state.memory.summary_email_override = address
    _ask_send(state, ctx, PendingQuestionKind.CONFIRM_ALT_EMAIL, address)
    return Parts(
        must=[
            "Read the new address back in full (it's the caller's own) and note it isn't the address on file.",
            "Ask them to confirm sending it there.",
        ],
        must_not=list(MUST_NOT),
        quick_replies=[QuickReply.SEND_SUMMARY.value, QuickReply.NO_THANKS.value],
        resume_anchor="confirm the alternate email address",
        fallback_reply=templates.alt_email_confirm(address),
    )


def _answer(state: SessionState, ctx: TurnContext, pending: PendingQuestion) -> StepResult:
    answer = ctx.answer(pending.kind, Phase.POST_PROCESS)
    if answer is Confirmation.YES and pending.on_yes is not None:
        # C2: stay in POST_PROCESS until the executor reports the result; PolicyEngine.settle() finishes
        return StepResult(parts=_sending_parts(), actions=[pending.on_yes])
    if answer is Confirmation.NO:
        if pending.kind is PendingQuestionKind.CONFIRM_ALT_EMAIL:
            return StepResult(parts=_offer(state, ctx))
        emit(state, ctx, EventType.EMAIL_SKIPPED, reason=SkipReason.DECLINED)  # C3
        return StepResult(parts=Parts(), next_phase=Phase.ENDED)
    if answer is Confirmation.UNCLEAR:
        unclear = pending.unclear_replies + 1
        if unclear >= VAGUE_LIMIT:  # C6: default is not to send
            emit(state, ctx, EventType.EMAIL_SKIPPED, reason=SkipReason.VAGUE)
            return StepResult(parts=Parts(), next_phase=Phase.ENDED)
        return StepResult(
            parts=_reask(state, ctx, pending, unclear, "Ask once more, simply, whether to send it.")
        )
    if ctx.nlu.questions:  # C5: what's in it, or another question about the offer
        must = "Summarize the draft's main points from GROUNDING, then ask again whether to send it."
        return StepResult(parts=_reask(state, ctx, pending, pending.unclear_replies, must))
    return StepResult(
        parts=_reask(state, ctx, pending, pending.unclear_replies, "Ask again whether to send it.")
    )


def _sending_parts() -> Parts:
    """Provisional parts; always replaced by settle_send() once the executor reports back."""
    return Parts(
        must=["Report the email result exactly as the events say."],
        must_not=list(MUST_NOT),
        resume_anchor="sending the summary email",
        fallback_reply=templates.EMAIL_SENDING,
    )


def settle_send(state: SessionState, ctx: TurnContext, action: PlannedAction, ok: bool) -> StepResult:
    """After the send: success ends the session; a failure stays here and offers a retry."""
    if ok:
        return StepResult(parts=Parts(), next_phase=Phase.ENDED)
    failures = int(action.params.get("send_failures", 0)) + 1
    if failures >= SEND_FAILURE_LIMIT:
        emit(state, ctx, EventType.EMAIL_SKIPPED, reason=SkipReason.SEND_FAILED)
        return StepResult(parts=Parts(), next_phase=Phase.ENDED)
    kind = PendingQuestionKind(
        str(action.params.get("offer_kind", PendingQuestionKind.OFFER_SUMMARY_EMAIL.value))
    )
    target = str(action.params.get("to", ""))
    _ask_send(state, ctx, kind, target, failures=failures)
    return StepResult(
        parts=Parts(
            must=[
                "Say the email couldn't be sent just now (the ACTION_FAILED event); never imply it was sent.",
                "Ask whether to try again or skip it.",
            ],
            must_not=list(MUST_NOT),
            quick_replies=[QuickReply.TRY_AGAIN.value, QuickReply.NO_THANKS.value],
            resume_anchor="retry the summary email",
            fallback_reply=templates.EMAIL_SEND_FAILED_RETRY,
        )
    )


def _reask(state: SessionState, ctx: TurnContext, pending: PendingQuestion, unclear: int, must: str) -> Parts:
    target = str(pending.payload.get("target", ""))
    if pending.kind is PendingQuestionKind.CONFIRM_ALT_EMAIL and target:
        _ask_send(state, ctx, pending.kind, target, unclear)
        fallback = templates.alt_email_confirm(target)
    else:
        return _offer(state, ctx, unclear, extra_must=[must])
    return Parts(
        must=[must],
        must_not=list(MUST_NOT),
        quick_replies=[QuickReply.SEND_SUMMARY.value, QuickReply.NO_THANKS.value],
        resume_anchor="confirm the alternate email address",
        fallback_reply=fallback,
    )
