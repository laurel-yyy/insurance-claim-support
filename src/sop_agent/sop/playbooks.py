"""Per-path handling guidance and follow-up items generated from data.

Follow-up items come from the claim data, not from the LLM's wording, so the summary email is traceable.
"""

from dataclasses import dataclass, field
from datetime import date

from sop_agent.domain.dates import DeadlineState, deadline_status
from sop_agent.domain.enums import ClaimStatus, Path
from sop_agent.domain.models import Claim, DocumentGuideline
from sop_agent.memory.state import FollowUp, FollowUpOwner
from sop_agent.sop.reasons import ReasonKey

PROCESSING_TIME_SETTING = "average_processing_time_after_submission"


@dataclass(frozen=True)
class PlaybookPlan:
    must: list[str]
    follow_ups: list[FollowUp] = field(default_factory=list)
    offer_live_agent: bool = False
    reason_key: ReasonKey | None = None


def _document_items(claim: Claim) -> list[FollowUp]:
    return [
        FollowUp(item=f"Submit the {name}", owner=FollowUpOwner.CALLER) for name in claim.documents_needed
    ]


def _deadline_item(claim: Claim, today: date) -> list[FollowUp]:
    status = deadline_status(claim.appeal_deadline, today)
    if status.state in (DeadlineState.OPEN, DeadlineState.TODAY):
        return [FollowUp(item="Appeal deadline", due_date=claim.appeal_deadline, owner=FollowUpOwner.CALLER)]
    return []


def _processing_item(claim: Claim, guideline: DocumentGuideline) -> list[FollowUp]:
    if not claim.documents_needed:
        return []
    timing = guideline.settings.get(PROCESSING_TIME_SETTING)
    detail = f" ({timing})" if timing else ""
    return [FollowUp(item=f"Review after the documents are received{detail}", owner=FollowUpOwner.INSURER)]


def _deadline_must(claim: Claim, today: date) -> list[str]:
    status = deadline_status(claim.appeal_deadline, today)
    if status.state is DeadlineState.NONE:
        return []
    if status.state is DeadlineState.PASSED:
        return [
            "Say the appeal deadline has passed without implying it is still available, and recommend a claims "
            "specialist review the options (reason: deadline_passed)."
        ]
    return ["State the appeal deadline and the days remaining exactly as computed in GROUNDING."]


def plan_for(
    path: Path, claim: Claim, guideline: DocumentGuideline, today: date, wants_appeal: bool
) -> PlaybookPlan:
    """Reply guidance and follow-up items for one path on one claim."""
    if path is Path.STATUS_INQUIRY:
        return _status(claim, guideline, today)
    if path is Path.DENIAL_QUESTION:
        return PlaybookPlan(
            must=[
                "Explain the denial reason from GROUNDING in plain language.",
                "List the documents the claim still needs.",
                *_deadline_must(claim, today),
                "Ask whether they'd like to know how to submit the documents.",
            ],
            follow_ups=_document_items(claim) + _deadline_item(claim, today),
        )
    if path is Path.DOCUMENT_SUBMISSION:
        return PlaybookPlan(
            must=[
                "Give the requirements for each needed document from GROUNDING.",
                "Explain how to submit, using the triggered follow-up topics in GROUNDING.",
                "If the caller lacks a document, give that document's alternatives from GROUNDING.",
            ],
            follow_ups=_document_items(claim) + _processing_item(claim, guideline),
        )
    if path is Path.NEXT_STEPS:
        return _next_steps(claim, guideline, today, wants_appeal)
    return PlaybookPlan(
        must=[
            "Answer from the claim data and the amount meanings in GROUNDING.",
            "Never promise a payment amount.",
        ]
    )


def _status(claim: Claim, guideline: DocumentGuideline, today: date) -> PlaybookPlan:
    must = ["State the current status and the claim summary from GROUNDING."]
    items: list[FollowUp] = []
    if claim.status is ClaimStatus.CLOSED:
        must.append("State the actual payment (net pay) and the maximum allowed amount from GROUNDING.")
    elif claim.status is ClaimStatus.OPEN:
        must.append(
            "Say it is still being processed; the expected reimbursement is only an expected figure, not a promise."
        )
    elif claim.status is ClaimStatus.DENIED:
        must += ["Say it was denied and which documents it needs.", *_deadline_must(claim, today)]
        items = _document_items(claim) + _deadline_item(claim, today)
    return PlaybookPlan(must=must, follow_ups=items)


def _next_steps(claim: Claim, guideline: DocumentGuideline, today: date, wants_appeal: bool) -> PlaybookPlan:
    passed = deadline_status(claim.appeal_deadline, today).state is DeadlineState.PASSED
    offer = wants_appeal
    if claim.status is ClaimStatus.DENIED and passed:
        must = [*_deadline_must(claim, today)]
        items: list[FollowUp] = [
            FollowUp(item="Claims specialist review of options", owner=FollowUpOwner.INSURER)
        ]
        offer, reason = True, ReasonKey.DEADLINE_PASSED
    elif claim.status is ClaimStatus.DENIED:
        must = ["Say the next step is to submit the needed documents.", *_deadline_must(claim, today)]
        items = _document_items(claim) + _deadline_item(claim, today) + _processing_item(claim, guideline)
        reason = None
    elif claim.status is ClaimStatus.OPEN:
        must, items, reason = (
            ["Say the claim is still being processed and nothing is needed right now."],
            [],
            None,
        )
    else:
        must, items, reason = ["Say nothing more is needed on this claim."], [], None
    if wants_appeal:
        must.append(
            "Explain that a formal appeal can't be filed here; say what the data supports and offer a transfer "
            "to a member of our claims team."
        )
    return PlaybookPlan(must=must, follow_ups=items, offer_live_agent=offer, reason_key=reason)
