"""Build the masked DebugView the Inspector shows (INV-9).

Identity fields appear only as provided/declined/missing. Everything else goes through the shared masking
function, except the email draft body, which is exactly what the verified caller is offered.
"""

from typing import Any

from sop_agent.api.schemas import DebugView
from sop_agent.domain.enums import IdentityField, IdentityStatus, Phase
from sop_agent.memory.state import SessionState
from sop_agent.observability.masking import mask_data, mask_email

TIMELINE_LIMIT = 200


def build_debug(state: SessionState, last_turn: dict[str, Any] | None) -> DebugView:
    identity = state.memory.identity
    provided = identity.valid_values()
    memory = state.memory
    rep = memory.representative
    turn = state.counters.turn_index
    return DebugView(
        phase=state.phase.value,
        verify_stage=state.verify_stage.value if state.phase is Phase.VERIFY_ID else None,
        identity_checklist={
            f.value: "provided" if f in provided else "declined" if f in identity.declined else "missing"
            for f in IdentityField
        },
        verification={
            "status": identity.status.value,
            "verified": identity.status is IdentityStatus.VERIFIED,
            "verified_as": identity.verified_as.value if identity.verified_as else None,
            "failed_attempts": identity.failed_attempts,
            "policy_number_given": identity.policy_number is not None,
        },
        representative={
            "caller_role": rep.caller_role.value,
            "authorization": rep.authorization.value,
            "consent_status": rep.consent_status.value,
            "consent_scenario": rep.consent_scenario,
        },
        memory=mask_data(
            {
                "hints": list(memory.case_hints.raw_mentions),
                "keywords": list(memory.case_hints.keywords),
                "intents": {p.value: round(c.confidence, 2) for p, c in memory.intent_candidates.items()},
                "deferred_questions": [
                    {"text": q.text, "answered": q.answered} for q in memory.deferred_questions
                ],
                "selected_case_id": memory.selected_case_id,
                "selected_path": memory.selected_path.value if memory.selected_path else None,
                "excluded_case_ids": sorted(memory.excluded_case_ids),
                "unavailable_documents": sorted(memory.unavailable_documents),
                "discussed_case_ids": list(memory.case_log.discussed_case_ids),
                "follow_ups": [f.item for f in memory.case_log.follow_ups],
            }
        ),
        counters=state.counters.model_dump(),
        pending_question=_pending(state),
        events_this_turn=[_event(e.model_dump(mode="json")) for e in state.events if e.turn == turn],
        timeline=[_event(e.model_dump(mode="json")) for e in state.events[-TIMELINE_LIMIT:]],
        last_turn=last_turn,
        email_draft=_draft(state),
        handoff=mask_data(state.handoff.model_dump(mode="json")) if state.handoff else None,
    )


def _event(event: dict[str, Any]) -> dict[str, Any]:
    masked: dict[str, Any] = mask_data(event)
    return masked


def _pending(state: SessionState) -> dict[str, Any] | None:
    pending = state.pending_question
    if pending is None:
        return None
    return {
        "kind": pending.kind.value,
        "asked_in_phase": pending.asked_in_phase.value,
        "on_yes": pending.on_yes.kind.value if pending.on_yes else None,
        "payload": mask_data(pending.payload),
    }


def _draft(state: SessionState) -> dict[str, Any] | None:
    draft = state.email_draft
    if draft is None:
        return None
    return {
        "to": mask_email(draft.to) if draft.to else None,
        "subject": draft.subject,
        "text": draft.text,
        "generated_by": draft.generated_by.value,
    }
