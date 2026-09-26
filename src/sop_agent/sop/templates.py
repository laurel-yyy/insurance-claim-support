"""Deterministic fallback replies (INV-8) and fixed copy.

Before verification these use only field labels, counts and fixed text, never record data. After verification
they may name the caller's own claim IDs. They are used whenever the LLM fails or the guard blocks a reply.
"""

from collections.abc import Sequence
from enum import StrEnum

from sop_agent.domain.enums import EscalationReason, IdentityField
from sop_agent.sop.directive import ActionKind

FIELD_LABELS: dict[IdentityField, str] = {
    IdentityField.FULL_NAME: "your full name",
    IdentityField.DOB: "your date of birth",
    IdentityField.PHONE: "the phone number on your account",
    IdentityField.EMAIL: "the email on your account",
    IdentityField.ID_LAST4: "the last four digits of your SSN or national ID",
}

FIELD_FORMAT_HINTS: dict[IdentityField, str] = {
    IdentityField.FULL_NAME: "your first and last name",
    IdentityField.DOB: "your full date of birth: month, day and year",
    IdentityField.PHONE: "the full 10-digit phone number",
    IdentityField.EMAIL: "the full email address",
    IdentityField.ID_LAST4: "exactly four digits",
}


class QuickReply(StrEnum):
    SEND_SUMMARY = "Send the summary"
    NO_THANKS = "No thanks"
    DIFFERENT_EMAIL = "Use a different email"
    CHECK_AGAIN = "Check again"
    LIVE_AGENT = "Talk to a live representative"
    CONTINUE_HERE = "Continue here"
    YES_SEND_REQUEST = "Yes, send the request"


def join_labels(labels: Sequence[str]) -> str:
    items = list(labels)
    if len(items) <= 1:
        return items[0] if items else ""
    return f"{', '.join(items[:-1])}, or {items[-1]}"


def field_options(fields: Sequence[IdentityField]) -> str:
    return join_labels([FIELD_LABELS[f] for f in fields])


def ask_more(count: int, fields: Sequence[IdentityField]) -> str:
    detail = "one more detail" if count == 1 else f"{count} more details"
    options = field_options(fields)
    choice = f" Could you share {detail}: {options}?" if options else f" Could you share {detail}?"
    return (
        "To protect your account, I need to verify your identity before I can look at any claim details."
        + choice
    )


def verification_failed(fields: Sequence[IdentityField]) -> str:
    extra = f" or share another detail, such as {field_options(fields)}" if fields else ""
    return f"I wasn't able to verify those details. Could you double-check what you shared{extra}?"


def invalid_field(field: IdentityField) -> str:
    return (
        f"I couldn't read {FIELD_LABELS[field]} in that format. Could you share {FIELD_FORMAT_HINTS[field]}?"
    )


VERIFICATION_INFEASIBLE = (
    "I understand. Without enough details I can't verify your identity here, which I need to do before "
    "discussing any account. A member of our claims team can help you another way. Would you like me to transfer you?"
)
VERIFIED = "Thank you, you're verified."
REP_NEED_INFO = (
    "Thanks. Since you're calling for someone else, could you tell me your full name and your relationship to "
    "the policyholder?"
)
REP_NOT_AUTHORIZED = (
    "I'm sorry, I'm not able to discuss this account with you. To protect the policyholder's privacy, we can only "
    "speak with them or someone they've authorized. The policyholder can call us directly, or register an "
    "authorized representative with a member of our claims team. Would you like me to transfer you?"
)
CONSENT_OFFER = (
    "Thanks. Before we continue, the policyholder needs to approve this call. May I text them a request to "
    "approve it now?"
)
CONSENT_WAITING = (
    "We're still waiting for the policyholder to approve the request. You can ask me to check again, or I can "
    "transfer you to a live representative."
)
CONSENT_REQUEST_DECLINED = (
    "Understood. Without the policyholder's approval I can't discuss the account. The policyholder can call us "
    "directly, or I can transfer you to a live representative. Would you like that?"
)
CONSENT_TIMEOUT = (
    "The policyholder hasn't approved the request, so I can't discuss the account right now. They can call us "
    "directly, or I can transfer you to a live representative. Would you like that?"
)
CONSENT_DECLINED = (
    "The policyholder didn't approve the request, so I can't discuss the account. I can transfer you to a live "
    "representative if you'd like."
)
NO_CLAIMS = (
    "I don't see any claims on file for this account. I can still answer general questions, or connect you with "
    "a member of our claims team, for example to start a new claim. Would you like me to transfer you?"
)
ASK_NEED = "How can I help with your claims today?"
ANYTHING_ELSE = "Is there anything else I can help you with?"
LIVE_AGENT_OFFER = "Would you like me to connect you with a member of our claims team?"
DECLINE = (
    "That's outside what I can help with here; I can only help with insurance claims and policy questions."
)
ABUSE_BOUNDARY = "I want to help, and I'll need us to keep the conversation respectful to do that."
EMAIL_OFFER = (
    "Would you like me to email a summary of this conversation to the email address on your account? "
    "You can also skip it."
)
EMAIL_REP_CANNOT_SWITCH = (
    "For privacy, the summary can only go to the email on the policyholder's file. Would you like me to send it "
    "there?"
)
EMAIL_INVALID = "That email address doesn't look complete. Could you share the full address?"
EMAIL_SENT_CLOSING = "Thanks for calling. Take care."
EMAIL_SKIPPED_CLOSING = "No problem, I won't send it. Thanks for calling, and take care."
EMAIL_VAGUE_SKIPPED = (
    "I'll skip the email for now; you can find the details in the member portal later. Thanks for calling."
)
TERMINAL_ESCALATED = "A member of our claims team will be with you shortly."
TERMINAL_ENDED = "This conversation has ended. Please start a new conversation if you need more help."


def alt_email_confirm(address: str) -> str:
    return (
        f"Just to confirm, you'd like the summary sent to {address}, which isn't the address on file. "
        "Should I send it there?"
    )


def choose_claim(case_ids: Sequence[str]) -> str:
    listed = "; ".join(f"{i}. {case_id}" for i, case_id in enumerate(case_ids, start=1))
    return f"I found a few claims that could match: {listed}. Which one would you like to talk about?"


def process_case(case_id: str) -> str:
    return (
        f"I have claim {case_id} open. I wasn't able to put together a full answer just now. What would you like "
        "to know first, or would you like me to connect you with a member of our claims team?"
    )


ESCALATION_REASON_TEXT: dict[EscalationReason, str] = {
    EscalationReason.SAFETY: (
        "If this is an emergency, please call 911 now, or call or text 988 for the Suicide and Crisis Lifeline. "
        "I'm connecting you with a member of our team."
    ),
    EscalationReason.CALLER_REQUEST: "I'm connecting you with a member of our claims team now.",
    EscalationReason.VERIFICATION_LOCKED: (
        "For your security, after several unsuccessful attempts a member of our claims team needs to help. "
        "I'm connecting you now."
    ),
    EscalationReason.OFF_TOPIC: "I'm connecting you with a member of our team who can help further.",
    EscalationReason.PERSUASION_EXHAUSTED: "I'm connecting you with a member of our claims team who can help.",
    EscalationReason.ABUSE: "I'm transferring you to a member of our team.",
    EscalationReason.DOCUMENT_ALTERNATIVES_EXHAUSTED: (
        "I'm connecting you with a claims specialist who can review manual options for your file."
    ),
}


def escalated(reason: EscalationReason, verified: bool) -> str:
    identity = "" if verified else " They'll also need to confirm your identity."
    return ESCALATION_REASON_TEXT[reason] + identity


ACTION_FAILURE_TEMPLATES: dict[ActionKind, str] = {
    ActionKind.REQUEST_CONSENT: "I wasn't able to send the approval request just now.",
    ActionKind.SEND_SUMMARY_EMAIL: "I wasn't able to send the email just now.",
    ActionKind.TRANSFER_TO_LIVE_AGENT: "I wasn't able to start the transfer just now.",
}
