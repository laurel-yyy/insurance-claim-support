"""Summary email drafting.

M4 ships `BasicSummaryDrafter`, a deterministic draft built only from the case log and claim data (no ID numbers,
DOB or phone). M5 replaces it with SummaryFacts + SummaryWriter + templates behind the same protocol (D47).
"""

from datetime import date
from typing import Protocol

from sop_agent.data.repository import InMemoryRepository
from sop_agent.memory.state import EmailDraft, SessionState


class SummaryDrafter(Protocol):
    def draft(self, state: SessionState, to: str) -> EmailDraft: ...


class BasicSummaryDrafter:
    def __init__(self, repo: InMemoryRepository, company_name: str) -> None:
        self._repo = repo
        self._company = company_name

    def draft(self, state: SessionState, to: str) -> EmailDraft:
        today: date = state.session_settings.today
        log = state.memory.case_log
        lines = [f"Here is a summary of your call with {self._company} on {today:%B %d, %Y}.", ""]
        claims = [c for c in (self._repo.claim(i) for i in log.discussed_case_ids) if c is not None]
        if claims:
            lines.append("Claims discussed:")
            for claim in claims:
                outcome = f" Reason: {claim.denial_reason}." if claim.denial_reason else ""
                lines.append(
                    f"- {claim.case_id} ({claim.case_type}, created {claim.created_at:%B %d, %Y}): "
                    f"{claim.raw_status}.{outcome}"
                )
            lines.append("")
        if log.follow_ups:
            lines.append("Next steps:")
            for item in log.follow_ups:
                due = f" by {item.due_date:%B %d, %Y}" if item.due_date else ""
                lines.append(f"- {item.item}{due} (owner: {item.owner.value})")
            lines.append("")
        lines.append(f"Thank you for contacting {self._company}.")
        return EmailDraft(
            to=to,
            subject=f"Summary of your call with {self._company} on {today:%B %d, %Y}",
            text="\n".join(lines),
        )
