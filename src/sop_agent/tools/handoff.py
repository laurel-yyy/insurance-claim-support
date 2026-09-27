"""LiveAgentHandoff: creates the ticket a live agent receives (SPEC §8.5), written to var/handoffs/."""

import json
import uuid
from datetime import datetime
from pathlib import Path

from sop_agent.domain.enums import EscalationReason, IdentityStatus
from sop_agent.memory.state import ChatRole, HandoffTicket, SessionState

SUMMARY_TURNS = 6


class LiveAgentHandoff:
    def __init__(self, directory: Path | None = None) -> None:
        self._directory = directory

    def create(self, state: SessionState, reason: EscalationReason, created_at: datetime) -> HandoffTicket:
        """`party_id` only when verification completed; hints and questions are the caller's own words."""
        identity = state.memory.identity
        verified = identity.status is IdentityStatus.VERIFIED
        recent = [
            f"{t.role.value}: {t.text}" for t in state.history[-SUMMARY_TURNS:] if t.role is ChatRole.CALLER
        ]
        ticket = HandoffTicket(
            ticket_id=f"HT-{uuid.uuid4().hex[:10]}",
            reason=reason,
            phase_at_escalation=state.phase,
            verified_as=identity.verified_as if verified else None,
            party_id=identity.party_id if verified else None,
            caller_stated_hints=list(state.memory.case_hints.raw_mentions),
            deferred_questions=[q.text for q in state.memory.deferred_questions if not q.answered],
            conversation_summary=" | ".join(recent),
            emotion_trend=[r.label for r in state.memory.emotion_history],
            created_at=created_at,
        )
        if self._directory is not None:
            self._directory.mkdir(parents=True, exist_ok=True)
            path = self._directory / f"{ticket.ticket_id}.json"
            path.write_text(json.dumps(ticket.model_dump(mode="json"), ensure_ascii=False), encoding="utf-8")
        return ticket
