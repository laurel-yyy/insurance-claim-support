"""ContextBuilder: the only place that assembles grounding, by phase scope (SPEC §9.1.3, INV-2).

`build()` calls only the builders for the scopes listed in PHASES[phase].context_scopes. VERIFY_ID lists no record
scope, so before verification no policyholder record data can reach a prompt, whatever the directive says.
"""

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sop_agent.data.repository import InMemoryRepository
from sop_agent.domain.dates import deadline_status
from sop_agent.domain.enums import CallerRole, IdentityField
from sop_agent.domain.models import Claim
from sop_agent.domain.money import format_usd
from sop_agent.memory.state import SessionState
from sop_agent.observability.masking import mask_email
from sop_agent.sop.directive import TurnDirective
from sop_agent.sop.guidance import document_bundle, render_template
from sop_agent.sop.phases import PHASES, ContextScope
from sop_agent.sop.verification import suggest_fields

AMOUNT_FIELDS = ("expected_reimbursement_amount", "allowed_max_amount", "net_pay", "net_fee")

Builder = Callable[["ContextBuilder", SessionState, TurnDirective], dict[str, Any]]


@dataclass(frozen=True)
class Grounding:
    data: dict[str, Any]
    scopes: tuple[ContextScope, ...]

    def as_json(self) -> str:
        return json.dumps(self.data, indent=1, ensure_ascii=False, default=str)


class ContextBuilder:
    def __init__(self, repo: InMemoryRepository, faq_text: str) -> None:
        self._repo = repo
        self._faq = faq_text

    def build(self, state: SessionState, directive: TurnDirective) -> Grounding:
        scopes = tuple(s for s in ContextScope if s in PHASES[state.phase].context_scopes)
        data = {scope.value: BUILDERS[scope](self, state, directive) for scope in scopes}
        return Grounding(data=data, scopes=scopes)

    # --- scopes without record data -----------------------------------------------------------------------

    def sop_status(self, state: SessionState, directive: TurnDirective) -> dict[str, Any]:
        """Field names and statuses only; never a value (V9)."""
        identity = state.memory.identity
        provided = identity.valid_values()
        checklist = {
            f.value: "provided" if f in provided else "declined" if f in identity.declined else "missing"
            for f in IdentityField
        }
        rep = state.memory.representative
        return {
            "phase": state.phase.value,
            "verify_stage": state.verify_stage.value,
            "today": state.session_settings.today.isoformat(),
            "identity_checklist": checklist,
            "fields_still_needed": directive.ask_for.count if directive.ask_for else 0,
            "fields_available_to_ask": [f.value for f in suggest_fields(identity)],
            "verification_status": identity.status.value,
            "representative_call": rep.caller_role is CallerRole.AUTHORIZED_REPRESENTATIVE,
            "authorization": rep.authorization.value,
            "consent": rep.consent_status.value,
        }

    def user_stated_hints(self, state: SessionState, directive: TurnDirective) -> dict[str, Any]:
        """Only what the caller said themselves (restating it is allowed before verification)."""
        memory = state.memory
        return {
            "caller_said": list(memory.case_hints.raw_mentions),
            "open_questions": [q.text for q in memory.deferred_questions if not q.answered],
        }

    def general_kb(self, state: SessionState, directive: TurnDirective) -> dict[str, Any]:
        """FAQ plus document guidance that isn't tied to a specific claim (§6.7)."""
        guideline = self._repo.document_guideline()
        return {
            "faq": self._faq,
            "default_document_guidance": guideline.default_guidance,
            "case_type_guidance": dict(guideline.case_type_guidance),
            "document_requirements": dict(guideline.document_guidance),
        }

    # --- record scopes (never listed for VERIFY_ID) ---------------------------------------------------------

    def party_profile(self, state: SessionState, directive: TurnDirective) -> dict[str, Any]:
        """How to address the caller, policy number, masked email, whether it's a representative session (§9.1.3)."""
        holder = self._repo.policyholder(state.memory.identity.party_id or "")
        if holder is None:
            return {}
        rep = state.memory.representative
        representative = rep.caller_role is CallerRole.AUTHORIZED_REPRESENTATIVE
        return {
            "policyholder_name": holder.name,
            "address_caller_as": rep.representative_name if representative else holder.name,
            "representative_session": representative,
            "relationship_to_policyholder": rep.relationship if representative else None,
            "policy_number": holder.policy_number,
            "email_on_file_masked": mask_email(holder.email),
        }

    def claim_summaries(self, state: SessionState, directive: TurnDirective) -> dict[str, Any]:
        claims = self._repo.claims_for(state.memory.identity.party_id or "")
        return {"claims": [_summary(c) for c in claims]}

    def selected_claim(self, state: SessionState, directive: TurnDirective) -> dict[str, Any]:
        claim = self._owned_selected(state)
        if claim is None:
            return {}
        glossary = self._repo.field_glossary()
        amounts = {
            name: {
                "value": format_usd(value),
                "meaning": glossary[name].description if name in glossary else None,
            }
            for name in AMOUNT_FIELDS
            if (value := getattr(claim, name)) is not None
        }
        status = deadline_status(claim.appeal_deadline, state.session_settings.today)
        return {
            **_summary(claim),
            "denial_reason": claim.denial_reason,
            "documents_needed": list(claim.documents_needed),
            "amounts": amounts,
            "appeal_deadline": {
                "date": status.deadline.isoformat() if status.deadline else None,
                "state": status.state.value,
                "days_remaining": status.days_remaining,
            },
            "today": state.session_settings.today.isoformat(),
            "path": state.memory.selected_path.value if state.memory.selected_path else None,
        }

    def claim_guidance(self, state: SessionState, directive: TurnDirective) -> dict[str, Any]:
        """K2 bundle plus the topics, fallback and alternatives the policy selected this turn."""
        claim = self._owned_selected(state)
        if claim is None:
            return {}
        guideline = self._repo.document_guideline()
        bundle = document_bundle(claim, guideline)
        request = directive.grounding
        topics = {t.topic: t for t in guideline.followup_topics}

        def rendered(names: list[str]) -> dict[str, str]:
            out: dict[str, str] = {}
            for name in names:
                topic = topics.get(name)
                text = render_template(topic.template, claim, guideline.settings) if topic else None
                if text is not None:
                    out[name] = text
            return out

        return {
            "documents": [
                {"name": d.name, "requirements": d.requirements or bundle.default_guidance}
                for d in bundle.documents
            ],
            "case_type_guidance": bundle.case_type_guidance,
            "default_guidance": bundle.default_guidance,
            "settings": dict(bundle.settings),
            "followup_topics": rendered(request.topics),
            "background_topics": rendered(request.background_topics),
            "followup_fallback": guideline.followup_fallback if request.use_followup_fallback else None,
            "alternatives": {
                d.name: d.alternatives for d in bundle.documents if d.name in request.alternatives_for
            },
        }

    def case_log(self, state: SessionState, directive: TurnDirective) -> dict[str, Any]:
        log = state.memory.case_log
        return {
            "discussed_case_ids": list(log.discussed_case_ids),
            "follow_ups": [
                {
                    "item": f.item,
                    "due_date": f.due_date.isoformat() if f.due_date else None,
                    "owner": f.owner.value,
                }
                for f in log.follow_ups
            ],
            "actions": [{"kind": a.kind.value, "ok": a.ok} for a in log.actions],
        }

    def summary_draft(self, state: SessionState, directive: TurnDirective) -> dict[str, Any]:
        draft = state.email_draft
        pending = state.pending_question
        target = str(pending.payload.get("target", "")) if pending else ""
        return {
            "draft": {"subject": draft.subject, "text": draft.text} if draft else None,
            "send_to_masked": mask_email(target) if target else None,
        }

    def _owned_selected(self, state: SessionState) -> Claim | None:
        case_id = state.memory.selected_case_id
        claim = self._repo.claim(case_id) if case_id else None
        if claim is None or claim.party_id != state.memory.identity.party_id:
            return None
        return claim


def _summary(claim: Claim) -> dict[str, Any]:
    return {
        "case_id": claim.case_id,
        "case_type": claim.case_type,
        "created_at": claim.created_at.isoformat(),
        "status": claim.raw_status,
        "summary": claim.summary,
    }


BUILDERS: dict[ContextScope, Builder] = {
    ContextScope.SOP_STATUS: ContextBuilder.sop_status,
    ContextScope.USER_STATED_HINTS: ContextBuilder.user_stated_hints,
    ContextScope.GENERAL_KB: ContextBuilder.general_kb,
    ContextScope.PARTY_PROFILE: ContextBuilder.party_profile,
    ContextScope.CLAIM_SUMMARIES: ContextBuilder.claim_summaries,
    ContextScope.SELECTED_CLAIM: ContextBuilder.selected_claim,
    ContextScope.CLAIM_GUIDANCE: ContextBuilder.claim_guidance,
    ContextScope.CASE_LOG: ContextBuilder.case_log,
    ContextScope.SUMMARY_DRAFT: ContextBuilder.summary_draft,
}
