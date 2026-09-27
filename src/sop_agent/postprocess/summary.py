"""Post-call email summary (SPEC §8.4.1-§8.4.2).

SummaryFacts are built by code only, from the case log, the discussed claims and executed actions. The
SummaryWriter (LLM) may only rephrase them; its draft is guarded (G2-G4 against the facts) and falls back to a
deterministic template. Neither ever includes ID numbers, DOB or phone numbers.
"""

import json
from datetime import date
from typing import Protocol

from pydantic import BaseModel

from sop_agent.data.repository import InMemoryRepository
from sop_agent.domain.enums import CallerRole, ClaimStatus
from sop_agent.domain.models import Claim
from sop_agent.domain.money import format_usd
from sop_agent.memory.state import DraftSource, EmailDraft, SessionState
from sop_agent.postprocess.render import render_email
from sop_agent.sop.directive import ActionKind

REFERENCE_CONTACT = "You can reply through the member portal or contact claims support with any questions."
AMOUNT_LABELS: dict[ClaimStatus, tuple[tuple[str, str], ...]] = {
    ClaimStatus.CLOSED: (("net_pay", "Amount paid"), ("allowed_max_amount", "Maximum allowed amount")),
    ClaimStatus.OPEN: (
        ("expected_reimbursement_amount", "Expected reimbursement (an estimate, not a promise)"),
    ),
}


def long_date(value: date) -> str:
    return f"{value:%B} {value.day}, {value.year}"


class ClaimOutcome(BaseModel):
    case_id: str
    case_type: str
    created: str
    status: str
    conclusion: str
    amounts: dict[str, str] = {}


class FollowUpFact(BaseModel):
    item: str
    due: str | None = None
    owner: str


class SummaryFacts(BaseModel):
    company: str
    call_date: str
    addressee: str
    representative_session: bool
    policyholder: str
    discussed: list[str]
    outcomes: list[ClaimOutcome]
    follow_ups: list[FollowUpFact]
    actions: list[str]
    claim_numbers: list[str]
    contact: str = REFERENCE_CONTACT

    def as_json(self) -> str:
        return json.dumps(self.model_dump(mode="json"), indent=1, ensure_ascii=False)


class EmailContent(BaseModel):
    """What the writer fills (§8.4.2). All fields required; the wire schema for structured output."""

    subject: str
    greeting: str
    discussed: list[str]
    outcomes: list[str]
    next_steps: list[str]
    closing: str


ACTION_FACTS: dict[ActionKind, str] = {
    ActionKind.REQUEST_CONSENT: "The policyholder was asked to approve this call.",
    ActionKind.TRANSFER_TO_LIVE_AGENT: "The call was transferred to a member of the claims team.",
}


def build_facts(state: SessionState, repo: InMemoryRepository, company: str) -> SummaryFacts:
    """Only the case log, the verified caller's own discussed claims and executed actions."""
    party_id = state.memory.identity.party_id
    holder = repo.policyholder(party_id or "")
    rep = state.memory.representative
    representative = rep.caller_role is CallerRole.AUTHORIZED_REPRESENTATIVE
    log = state.memory.case_log
    claims = [
        c for c in (repo.claim(i) for i in log.discussed_case_ids) if c is not None and c.party_id == party_id
    ]
    policyholder = holder.name if holder else ""
    return SummaryFacts(
        company=company,
        call_date=long_date(state.session_settings.today),
        addressee=(rep.representative_name or policyholder) if representative else policyholder,
        representative_session=representative,
        policyholder=policyholder,
        discussed=list(dict.fromkeys(log.questions_answered)),
        outcomes=[_outcome(c) for c in claims],
        follow_ups=[
            FollowUpFact(item=f.item, due=long_date(f.due_date) if f.due_date else None, owner=f.owner.value)
            for f in log.follow_ups
        ],
        actions=[ACTION_FACTS[a.kind] for a in log.actions if a.ok and a.kind in ACTION_FACTS],
        claim_numbers=[c.case_id for c in claims],
    )


def _outcome(claim: Claim) -> ClaimOutcome:
    amounts = {
        label: format_usd(value)
        for field, label in AMOUNT_LABELS.get(claim.status, ())
        if (value := getattr(claim, field)) is not None
    }
    return ClaimOutcome(
        case_id=claim.case_id,
        case_type=claim.case_type,
        created=long_date(claim.created_at),
        status=claim.raw_status,
        conclusion=f"Denied because {claim.denial_reason}." if claim.denial_reason else claim.summary,
        amounts=amounts,
    )


def template_content(facts: SummaryFacts) -> EmailContent:
    """Deterministic content straight from the facts: the fallback when the writer fails or is blocked."""
    outcomes = []
    for o in facts.outcomes:
        amounts = "".join(f" {label}: {value}." for label, value in o.amounts.items())
        outcomes.append(
            f"{o.case_id} ({o.case_type}, created {o.created}): {o.status}. {o.conclusion}{amounts}"
        )
    steps = [f"{f.item}{f' by {f.due}' if f.due else ''} (owner: {f.owner})" for f in facts.follow_ups]
    return EmailContent(
        subject=subject_for(facts),
        greeting=f"Hello {facts.addressee}," if facts.addressee else "Hello,",
        discussed=facts.discussed or ["Your claims and next steps."],
        outcomes=outcomes,
        next_steps=steps + facts.actions,
        closing=f"Thank you for contacting {facts.company}. {facts.contact}",
    )


def subject_for(facts: SummaryFacts) -> str:
    return f"Summary of your call with {facts.company} on {facts.call_date}"


def draft_from(content: EmailContent, facts: SummaryFacts, to: str, source: DraftSource) -> EmailDraft:
    """Render text and HTML; the subject always follows §8.4.1, whatever the writer produced."""
    content = content.model_copy(update={"subject": subject_for(facts)})
    text, html = render_email(content.model_dump(), facts.claim_numbers)
    return EmailDraft(to=to, subject=content.subject, text=text, html=html, generated_by=source)


class SummaryDrafter(Protocol):
    def draft(self, state: SessionState, to: str) -> EmailDraft: ...


class TemplateDrafter:
    """Synchronous deterministic drafter (used by the executor if a send happens with no draft)."""

    def __init__(self, repo: InMemoryRepository, company: str) -> None:
        self._repo = repo
        self._company = company

    def draft(self, state: SessionState, to: str) -> EmailDraft:
        facts = build_facts(state, self._repo, self._company)
        return draft_from(template_content(facts), facts, to, DraftSource.TEMPLATE)
