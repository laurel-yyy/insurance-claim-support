"""Read-only tools exposed to the responder (SPEC §11.1). There are no write tools (INV-5).

`party_id` is never a parameter: it comes from session state through ToolContext (INV-4). Every input
parameter is required, so the schemas work as strict tools.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date
from typing import Any

from sop_agent.data.repository import InMemoryRepository
from sop_agent.domain.dates import deadline_status
from sop_agent.domain.enums import Phase
from sop_agent.domain.models import Claim
from sop_agent.domain.money import format_usd
from sop_agent.llm.base import ToolSpec
from sop_agent.sop.guidance import document_bundle, render_template
from sop_agent.sop.phases import ToolName

NOT_AVAILABLE = "No claim with that ID is available for this account."
UNKNOWN_TOPIC = "No follow-up guidance with that topic name exists."
AMOUNT_FIELDS = ("expected_reimbursement_amount", "allowed_max_amount", "net_pay", "net_fee")


class ToolInputError(Exception):
    """A tool was called with inputs it can't use; the message is safe to show the model."""


@dataclass(frozen=True)
class ToolContext:
    party_id: str
    phase: Phase
    today: date


def _object(properties: dict[str, dict[str, Any]]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


CASE_ID = {"case_id": {"type": "string", "description": "Claim number, e.g. as listed by list_claims"}}

SPECS: dict[ToolName, ToolSpec] = {
    ToolName.LIST_CLAIMS: ToolSpec(
        name=ToolName.LIST_CLAIMS.value,
        description="List the verified caller's claims: claim number, type, creation date, status, summary.",
        input_schema=_object({}),
    ),
    ToolName.GET_CLAIM: ToolSpec(
        name=ToolName.GET_CLAIM.value,
        description="Full data for one of the caller's claims, with formatted amounts, their meanings and deadline status.",
        input_schema=_object(CASE_ID),
    ),
    ToolName.GET_DOCUMENT_GUIDANCE: ToolSpec(
        name=ToolName.GET_DOCUMENT_GUIDANCE.value,
        description="Document requirements and alternatives for one of the caller's claims.",
        input_schema=_object(CASE_ID),
    ),
    ToolName.GET_FOLLOWUP_GUIDANCE: ToolSpec(
        name=ToolName.GET_FOLLOWUP_GUIDANCE.value,
        description="Rendered follow-up guidance for a claim and topic; returns the general answer if none applies.",
        input_schema=_object({**CASE_ID, "topic": {"type": "string", "description": "Follow-up topic name"}}),
    ),
}

ToolFn = Callable[["ReadTools", ToolContext, dict[str, Any]], Awaitable[dict[str, Any]]]


class ReadTools:
    def __init__(self, repo: InMemoryRepository) -> None:
        self._repo = repo

    def owned_claim(self, ctx: ToolContext, args: dict[str, Any]) -> Claim:
        """Ownership check: "doesn't exist" and "isn't yours" are the same error (INV-4)."""
        case_id = str(args.get("case_id", "")).strip().upper()
        claim = self._repo.claim(case_id)
        if claim is None or claim.party_id != ctx.party_id:
            raise ToolInputError(NOT_AVAILABLE)
        return claim

    async def list_claims(self, ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
        claims = self._repo.claims_for(ctx.party_id)
        return {"claims": [_summary(c) for c in claims]}

    async def get_claim(self, ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
        claim = self.owned_claim(ctx, args)
        glossary = self._repo.field_glossary()
        status = deadline_status(claim.appeal_deadline, ctx.today)
        return {
            **_summary(claim),
            "denial_reason": claim.denial_reason,
            "documents_needed": list(claim.documents_needed),
            "amounts": {
                name: {
                    "value": format_usd(v),
                    "meaning": glossary[name].description if name in glossary else None,
                }
                for name in AMOUNT_FIELDS
                if (v := getattr(claim, name)) is not None
            },
            "appeal_deadline": {
                "date": status.deadline.isoformat() if status.deadline else None,
                "state": status.state.value,
                "days_remaining": status.days_remaining,
            },
        }

    async def get_document_guidance(self, ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
        claim = self.owned_claim(ctx, args)
        bundle = document_bundle(claim, self._repo.document_guideline())
        return {
            "case_id": claim.case_id,
            "documents": [
                {
                    "name": d.name,
                    "requirements": d.requirements or bundle.default_guidance,
                    "alternatives": d.alternatives,
                }
                for d in bundle.documents
            ],
            "case_type_guidance": bundle.case_type_guidance,
            "default_guidance": bundle.default_guidance,
            "settings": dict(bundle.settings),
        }

    async def get_followup_guidance(self, ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
        claim = self.owned_claim(ctx, args)
        guideline = self._repo.document_guideline()
        wanted = str(args.get("topic", "")).strip().casefold()
        topic = next((t for t in guideline.followup_topics if t.topic.casefold() == wanted), None)
        if topic is None:
            raise ToolInputError(UNKNOWN_TOPIC)
        if topic.requires_documents and not claim.documents_needed:
            return {"case_id": claim.case_id, "topic": topic.topic, "text": guideline.followup_fallback}
        text = render_template(topic.template, claim, guideline.settings)
        return {"case_id": claim.case_id, "topic": topic.topic, "text": text or guideline.followup_fallback}


def _summary(claim: Claim) -> dict[str, Any]:
    return {
        "case_id": claim.case_id,
        "case_type": claim.case_type,
        "created_at": claim.created_at.isoformat(),
        "status": claim.raw_status,
        "summary": claim.summary,
    }


FUNCTIONS: dict[ToolName, ToolFn] = {
    ToolName.LIST_CLAIMS: ReadTools.list_claims,
    ToolName.GET_CLAIM: ReadTools.get_claim,
    ToolName.GET_DOCUMENT_GUIDANCE: ReadTools.get_document_guidance,
    ToolName.GET_FOLLOWUP_GUIDANCE: ReadTools.get_followup_guidance,
}
