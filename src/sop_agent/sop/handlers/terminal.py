"""ESCALATED and ENDED (SPEC §8.5). Terminal phases never advance (INV-7) and answer with fixed copy."""

from sop_agent.domain.enums import EscalationReason, IdentityStatus, Phase
from sop_agent.memory.state import SessionState
from sop_agent.sop import templates
from sop_agent.sop.directive import EventType
from sop_agent.sop.handlers.base import Parts, StepResult, TurnContext, emit
from sop_agent.sop.handlers.post_process import SkipReason


def escalated_parts(state: SessionState, reason: EscalationReason) -> Parts:
    verified = state.memory.identity.status is IdentityStatus.VERIFIED
    must = [
        f"Tell the caller they're being connected with a member of our claims team (reason: {reason.value})."
    ]
    if reason is EscalationReason.SAFETY:
        must.insert(0, "Give safety information first: call 911 in an emergency; call or text 988 in the US.")
    if not verified:
        must.append("Explain the live agent will also need to confirm their identity.")
    return Parts(
        must=must,
        must_not=["Say the transfer happened unless ESCALATED is in events."],
        resume_anchor="transfer to a live agent",
        fallback_reply=templates.escalated(reason, verified),
    )


def _ended_parts(state: SessionState, ctx: TurnContext) -> Parts:
    skipped = [e for e in state.events if e.turn == ctx.turn and e.type is EventType.EMAIL_SKIPPED]
    if skipped:
        reason = str(skipped[-1].data.get("reason", ""))
        portal = reason in (SkipReason.VAGUE, SkipReason.SEND_FAILED)
        closings: dict[str, str] = {
            SkipReason.VAGUE: templates.EMAIL_VAGUE_SKIPPED,
            SkipReason.SEND_FAILED: templates.EMAIL_SEND_FAILED_CLOSING,
        }
        fallback = closings.get(reason, templates.EMAIL_SKIPPED_CLOSING)
        must = ["Close warmly and briefly; the email won't be sent."]
        if reason == SkipReason.SEND_FAILED:
            must.insert(0, "Say the email still couldn't be sent, so it's skipped for now.")
        if portal:
            must.append("Mention they can find the details in the member portal later.")
    else:
        fallback = templates.EMAIL_SENT_CLOSING
        must = [
            "Confirm the summary was sent (EMAIL_SENT is in events).",
            "Close warmly and briefly.",
        ]
    return Parts(
        must=must,
        must_not=["Say the email was sent unless EMAIL_SENT is in events."],
        resume_anchor="conversation closed",
        fallback_reply=fallback,
        max_sentences=3,
    )


class TerminalHandler:
    def step(self, state: SessionState, ctx: TurnContext, entering: bool) -> StepResult:
        if state.phase is Phase.ENDED:
            if entering:
                emit(state, ctx, EventType.SESSION_ENDED)
                return StepResult(parts=_ended_parts(state, ctx))
            return StepResult(parts=_fixed(templates.TERMINAL_ENDED))
        return StepResult(parts=_fixed(templates.TERMINAL_ESCALATED))


def _fixed(text: str) -> Parts:
    return Parts(
        must=[f"Reply exactly: {text}"], resume_anchor="session closed", fallback_reply=text, max_sentences=2
    )
