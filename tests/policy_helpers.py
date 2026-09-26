"""Drive PolicyEngine turns in tests and check directive invariants on every decision."""

from typing import Any

from sop_agent.data.repository import InMemoryRepository
from sop_agent.domain.dates import DateHint
from sop_agent.domain.enums import (
    CallerRole,
    ClaimStatus,
    IdentityField,
    IdentityStatus,
    Path,
    Phase,
    VerifiedAs,
)
from sop_agent.memory.state import SessionState
from sop_agent.nlu.schema import Confirmation, DialogAct, IntentScore, NLUResult, Question, QuestionKind
from sop_agent.sop.directive import (
    ConsentObservation,
    EventType,
    Observations,
    PolicyDecision,
    SelectorChoice,
)
from sop_agent.sop.handlers.base import PolicyConfig
from sop_agent.sop.policy import PolicyEngine
from tests.builders import nlu

F = IdentityField


def engine(repo: InMemoryRepository) -> PolicyEngine:
    return PolicyEngine(repo, PolicyConfig())


def record_tokens(repo: InMemoryRepository) -> set[str]:
    """Record values that must never appear in a pre-verification reply (a test-side G1 approximation)."""
    found: set[str] = set()
    for holder in repo.policyholders():
        found |= {
            holder.policy_number,
            holder.email,
            holder.phone[-10:],
            holder.dob.isoformat(),
            holder.id_last4,
        }
        found |= {f"{holder.dob:%B} {holder.dob.day}, {holder.dob.year}", f"{holder.dob:%m/%d/%Y}"}
    for claim in repo.claims():
        found |= {claim.case_id, claim.created_at.isoformat()}
        if claim.appeal_deadline:
            found.add(claim.appeal_deadline.isoformat())
        if claim.denial_reason:
            found.add(claim.denial_reason)
        for amount in (claim.allowed_max_amount, claim.net_fee, claim.expected_reimbursement_amount):
            if amount:
                found.add(f"{amount:,.2f}")
    return {t.casefold() for t in found}


def check_directive(decision: PolicyDecision, repo: InMemoryRepository) -> None:
    directive = decision.directive
    assert directive.resume_anchor.strip(), "every directive needs a resume_anchor"
    assert directive.fallback_reply.strip(), "every directive needs a fallback_reply (INV-8)"
    assert directive.phase is decision.state.phase
    if decision.state.memory.identity.status is not IdentityStatus.VERIFIED:
        reply = directive.fallback_reply.casefold()
        leaked = [t for t in record_tokens(repo) if t and t in reply]
        assert not leaked, f"pre-verification fallback leaks record data: {leaked}"


def turn(
    eng: PolicyEngine,
    repo: InMemoryRepository,
    state: SessionState,
    message: NLUResult | None = None,
    observations: Observations | None = None,
) -> PolicyDecision:
    before = state.model_dump()
    decision = eng.decide(state, message or nlu(), observations or Observations())
    assert state.model_dump() == before, "decide() must never mutate its input state"
    check_directive(decision, repo)
    return decision


def types(decision: PolicyDecision) -> list[EventType]:
    return [e.type for e in decision.events]


def verified(state: SessionState, party_id: str, phase: Phase = Phase.RESOLVE_INTENT) -> SessionState:
    state = state.model_copy(deep=True)
    identity = state.memory.identity
    identity.status, identity.party_id, identity.verified_as = (
        IdentityStatus.VERIFIED,
        party_id,
        VerifiedAs.POLICYHOLDER,
    )
    state.memory.representative.caller_role = CallerRole.POLICYHOLDER
    state.phase = phase
    return state


def margaret_message(**extra: Any) -> NLUResult:
    return nlu(
        {F.FULL_NAME: "Margaret Chen", F.DOB: "1985-03-15", F.ID_LAST4: "4472"},
        policy_number="POL-9921",
        caller_role=CallerRole.POLICYHOLDER,
        case_type="healthcare",
        claim_status=ClaimStatus.DENIED,
        date_hint=DateHint(month=1),
        intents=[IntentScore(path=Path.DENIAL_QUESTION, confidence=0.7)],
        dialog_acts=[DialogAct.PROVIDE_IDENTITY, DialogAct.STATE_NEED],
        **extra,
    )


def yes() -> NLUResult:
    return nlu(confirmation=Confirmation.YES, dialog_acts=[DialogAct.CONFIRM])


def no() -> NLUResult:
    return nlu(confirmation=Confirmation.NO, dialog_acts=[DialogAct.DENY])


def question(text: str, kind: QuestionKind) -> Question:
    return Question(text=text, kind=kind)


def consent(status: str, exhausted: bool = False) -> Observations:
    return Observations(consent=ConsentObservation(status=status, exhausted=exhausted))


def selector(case_id: str, path: Path | None = None) -> Observations:
    return Observations(claim_selection=SelectorChoice(case_id=case_id, path=path))
