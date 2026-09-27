"""ActionExecutor: runs only actions the policy planned after explicit confirmation (SPEC §11.3, INV-5).

Preconditions are re-checked here; a failed check or a failed side effect adds ACTION_FAILED and the result goes
back to the orchestrator (and to PolicyEngine.settle for emails). It never reports success it didn't achieve.
"""

from dataclasses import dataclass
from typing import Any

from sop_agent.data.repository import InMemoryRepository
from sop_agent.domain.clock import Clock
from sop_agent.domain.enums import AuthorizationStatus, CallerRole, ConsentStatus, EscalationReason, Phase
from sop_agent.domain.normalize import normalize_email
from sop_agent.memory.state import ActionRecord, SessionState
from sop_agent.observability.logging import get_logger
from sop_agent.postprocess.summary import SummaryDrafter
from sop_agent.sop.directive import ActionKind, ActionResult, Event, EventType, PlannedAction
from sop_agent.tools.consent import ConsentError, ScenarioConsentService
from sop_agent.tools.email import EmailService
from sop_agent.tools.handoff import LiveAgentHandoff

_log = get_logger(__name__)


class PreconditionError(Exception):
    """A planned action's preconditions don't hold any more; nothing was done."""


@dataclass
class ActionExecutor:
    repo: InMemoryRepository
    clock: Clock
    consent: ScenarioConsentService
    outbox: EmailService
    handoff: LiveAgentHandoff
    drafter: SummaryDrafter

    def run(
        self, actions: list[PlannedAction], state: SessionState
    ) -> tuple[SessionState, list[ActionResult]]:
        new = state.model_copy(deep=True)
        results: list[ActionResult] = []
        for action in actions:
            try:
                detail = self._run_one(action, new)
            except (PreconditionError, ConsentError, OSError) as exc:
                _log.warning(
                    "action failed",
                    extra={"fields": {"kind": action.kind.value, "error": type(exc).__name__}},
                )
                _emit(new, EventType.ACTION_FAILED, action=action.kind.value)
                results.append(ActionResult(action=action, ok=False, detail=type(exc).__name__))
                new.memory.case_log.actions.append(
                    ActionRecord(kind=action.kind, turn=new.counters.turn_index, ok=False)
                )
                continue
            _emit(new, EventType.ACTION_EXECUTED, action=action.kind.value)
            results.append(ActionResult(action=action, ok=True, detail=detail))
            new.memory.case_log.actions.append(
                ActionRecord(kind=action.kind, turn=new.counters.turn_index, ok=True)
            )
        return new, results

    def _run_one(self, action: PlannedAction, state: SessionState) -> str:
        if action.kind is ActionKind.REQUEST_CONSENT:
            return self._request_consent(state, action.params)
        if action.kind is ActionKind.SEND_SUMMARY_EMAIL:
            return self._send_email(state, action.params)
        return self._transfer(state, action.params)

    def _request_consent(self, state: SessionState, params: dict[str, Any]) -> str:
        rep = state.memory.representative
        party_id = state.memory.identity.party_id
        if rep.authorization is not AuthorizationStatus.MATCHED or party_id is None:
            raise PreconditionError("authorization record not matched")
        scenario = str(params.get("consent_scenario") or state.session_settings.consent_scenario)
        request_id, first = self.consent.request(party_id, scenario)
        rep.consent_request_id, rep.consent_status = request_id, ConsentStatus.PENDING
        _emit(state, EventType.CONSENT_REQUESTED)
        _emit(state, EventType.CONSENT_STATUS, status=first.status)
        return request_id

    def _send_email(self, state: SessionState, params: dict[str, Any]) -> str:
        """C4/A8: a valid address; representatives can only send to the policyholder's address on file."""
        if state.phase is not Phase.POST_PROCESS:
            raise PreconditionError("not in POST_PROCESS")
        to = normalize_email(str(params.get("to", "")))
        holder = self.repo.policyholder(state.memory.identity.party_id or "")
        if to is None or holder is None:
            raise PreconditionError("no valid target address")
        representative = state.memory.representative.caller_role is CallerRole.AUTHORIZED_REPRESENTATIVE
        if representative and to != normalize_email(holder.email):
            raise PreconditionError("representatives can only use the address on file")
        if state.email_draft is None:  # Normally drafted on entering POST_PROCESS; never send without one
            state.email_draft = self.drafter.draft(state, to)
            _emit(state, EventType.SUMMARY_DRAFTED, generated_by=state.email_draft.generated_by.value)
        elif state.email_draft.to != to:  # C4: same content, confirmed new recipient
            state.email_draft = state.email_draft.model_copy(update={"to": to})
        draft = state.email_draft
        record = self.outbox.send(
            state.session_id, to, draft.subject, draft.text, draft.html, self.clock.now()
        )
        _emit(state, EventType.EMAIL_SENT)
        return record.message_id

    def _transfer(self, state: SessionState, params: dict[str, Any]) -> str:
        reason = EscalationReason(str(params.get("reason", EscalationReason.CALLER_REQUEST.value)))
        ticket = self.handoff.create(state, reason, self.clock.now())
        state.handoff = ticket
        _emit(state, EventType.ESCALATED, reason=reason.value)
        return ticket.ticket_id


def _emit(state: SessionState, kind: EventType, **data: Any) -> None:
    state.events.append(Event(type=kind, turn=state.counters.turn_index, phase=state.phase, data=dict(data)))
