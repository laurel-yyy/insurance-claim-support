"""VERIFY_ID for authorized representatives: authorization then real-time consent (SPEC §8.1.7, A3-A6).

The account is discussed only after the authorization record matches and the policyholder approves. Consent
status arrives as an observation; the request itself is a planned action run by the executor (INV-5).
"""

from sop_agent.domain.enums import (
    AuthorizationStatus,
    ConsentStatus,
    IdentityStatus,
    Phase,
    VerifiedAs,
    VerifyStage,
)
from sop_agent.memory.state import SessionState
from sop_agent.nlu.schema import Confirmation
from sop_agent.sop import templates
from sop_agent.sop.authorization import check_authorization
from sop_agent.sop.directive import ActionKind, EventType, PendingQuestionKind, PlannedAction
from sop_agent.sop.handlers.base import Parts, StepResult, TurnContext, ask, emit
from sop_agent.sop.templates import QuickReply

MUST_NOT = [
    "Share, confirm or hint at any claim, policy or account information.",
    "Reveal whether other representatives are registered, or any digit of the policyholder's phone number.",
]
WAITING_REPLIES = [QuickReply.CHECK_AGAIN.value, QuickReply.LIVE_AGENT.value]
OFFER_REPLIES = [QuickReply.LIVE_AGENT.value, QuickReply.CONTINUE_HERE.value]


def step(state: SessionState, ctx: TurnContext) -> StepResult:
    if state.verify_stage is VerifyStage.AUTHORIZATION:
        return _authorization(state, ctx)
    return _consent(state, ctx)


def _parts(
    *, must: list[str], resume_anchor: str, fallback_reply: str, quick_replies: list[str] | None = None
) -> Parts:
    return Parts(
        must=must,
        must_not=list(MUST_NOT),
        resume_anchor=resume_anchor,
        fallback_reply=fallback_reply,
        quick_replies=quick_replies or [],
    )


def _offer_live_agent(state: SessionState, ctx: TurnContext, parts: Parts) -> Parts:
    parts.offer_live_agent = True
    parts.quick_replies = list(OFFER_REPLIES)
    ask(state, ctx, PendingQuestionKind.OFFER_LIVE_AGENT)
    return parts


def _authorization(state: SessionState, ctx: TurnContext) -> StepResult:
    rep = state.memory.representative
    if rep.authorization is AuthorizationStatus.NOT_AUTHORIZED:
        return StepResult(parts=_not_authorized_parts(state, ctx))  # Final for the session (A4)
    party_id = state.memory.identity.party_id or ""
    result = check_authorization(party_id, rep.representative_name, rep.relationship, ctx.repo)
    rep.authorization = result.status
    if result.status is AuthorizationStatus.NEEDS_INFO:
        missing = ", ".join(m.value.replace("_", " ") for m in result.missing)
        return StepResult(
            parts=_parts(
                must=[f"Ask for the caller's {missing} before going further."],
                resume_anchor="representative authorization: details needed",
                fallback_reply=templates.REP_NEED_INFO,
            )
        )
    if result.status is AuthorizationStatus.NOT_AUTHORIZED:
        emit(state, ctx, EventType.REP_NOT_AUTHORIZED)
        return StepResult(parts=_not_authorized_parts(state, ctx))
    state.verify_stage = VerifyStage.CONSENT
    return StepResult(parts=_consent_offer_parts(state, ctx))


def _not_authorized_parts(state: SessionState, ctx: TurnContext) -> Parts:
    parts = _parts(
        must=[
            "Gently explain the account can't be discussed (reason: representative_authorization).",
            "Suggest the policyholder call directly or register an authorized representative with a live agent.",
            "Offer a transfer to a member of our claims team.",
        ],
        resume_anchor="representative not authorized",
        fallback_reply=templates.REP_NOT_AUTHORIZED,
    )
    return _offer_live_agent(state, ctx, parts)


def _consent_offer_parts(state: SessionState, ctx: TurnContext) -> Parts:
    ask(
        state,
        ctx,
        PendingQuestionKind.CONFIRM_CONSENT_REQUEST,
        on_yes=PlannedAction(
            kind=ActionKind.REQUEST_CONSENT,
            params={"consent_scenario": state.memory.representative.consent_scenario},
        ),
    )
    return _parts(
        must=[
            "Explain the policyholder must approve this call in real time (reason: policyholder_consent).",
            "Ask whether to text the policyholder a request to approve it now.",
        ],
        quick_replies=[QuickReply.YES_SEND_REQUEST.value, QuickReply.LIVE_AGENT.value],
        resume_anchor="policyholder consent: ask to send the approval request",
        fallback_reply=templates.CONSENT_OFFER,
    )


def _consent(state: SessionState, ctx: TurnContext) -> StepResult:
    rep = state.memory.representative
    if rep.consent_status in (ConsentStatus.TIMEOUT, ConsentStatus.DECLINED):
        return StepResult(parts=_consent_ended_parts(state, ctx, rep.consent_status))
    if rep.consent_status is ConsentStatus.PENDING:
        return _poll(state, ctx)
    answer = ctx.answer(PendingQuestionKind.CONFIRM_CONSENT_REQUEST, Phase.VERIFY_ID)
    if answer is Confirmation.YES and ctx.pending is not None and ctx.pending.on_yes is not None:
        return StepResult(
            parts=_parts(
                must=[
                    "If events include CONSENT_REQUESTED, say the approval request was sent and we're waiting.",
                    "Never say it was sent unless CONSENT_REQUESTED is in events.",
                ],
                quick_replies=list(WAITING_REPLIES),
                resume_anchor="waiting for the policyholder's approval",
                fallback_reply=templates.CONSENT_WAITING,
            ),
            actions=[ctx.pending.on_yes],
        )
    if answer is Confirmation.NO:
        parts = _parts(
            must=["Explain the account can't be discussed without the policyholder's approval."],
            resume_anchor="consent request declined by caller",
            fallback_reply=templates.CONSENT_REQUEST_DECLINED,
        )
        return StepResult(parts=_offer_live_agent(state, ctx, parts))
    return StepResult(parts=_consent_offer_parts(state, ctx))


def _poll(state: SessionState, ctx: TurnContext) -> StepResult:
    rep = state.memory.representative
    observed = ctx.observations.consent
    status = observed.status.casefold() if observed else ConsentStatus.PENDING.value
    if observed is not None:
        emit(state, ctx, EventType.CONSENT_STATUS, status=status)
    if status == ConsentStatus.APPROVED.value:
        rep.consent_status = ConsentStatus.APPROVED
        identity = state.memory.identity
        identity.status, identity.verified_as = IdentityStatus.VERIFIED, VerifiedAs.REPRESENTATIVE
        emit(state, ctx, EventType.CONSENT_APPROVED)
        emit(state, ctx, EventType.VERIFIED)
        return StepResult(
            parts=_parts(
                must=["Say the policyholder approved and refer to them in the third person from now on."],
                resume_anchor="verified",
                fallback_reply=templates.VERIFIED,
            ),
            next_phase=Phase.RESOLVE_INTENT,
        )
    if status != ConsentStatus.PENDING.value:
        rep.consent_status = ConsentStatus.DECLINED
        emit(state, ctx, EventType.CONSENT_DECLINED)
        return StepResult(parts=_consent_ended_parts(state, ctx, ConsentStatus.DECLINED))
    if observed is not None and observed.exhausted:
        rep.consent_status = ConsentStatus.TIMEOUT
        emit(state, ctx, EventType.CONSENT_TIMEOUT)
        return StepResult(parts=_consent_ended_parts(state, ctx, ConsentStatus.TIMEOUT))
    return StepResult(
        parts=_parts(
            must=["Say we're still waiting for the policyholder's approval."],
            quick_replies=list(WAITING_REPLIES),
            resume_anchor="waiting for the policyholder's approval",
            fallback_reply=templates.CONSENT_WAITING,
        )
    )


def _consent_ended_parts(state: SessionState, ctx: TurnContext, status: ConsentStatus) -> Parts:
    timed_out = status is ConsentStatus.TIMEOUT
    parts = _parts(
        must=[
            "Explain the account can't be discussed without the policyholder's approval.",
            "Suggest the policyholder call directly, or offer a transfer to a live representative.",
        ],
        resume_anchor="consent not given",
        fallback_reply=templates.CONSENT_TIMEOUT if timed_out else templates.CONSENT_DECLINED,
    )
    return _offer_live_agent(state, ctx, parts)
