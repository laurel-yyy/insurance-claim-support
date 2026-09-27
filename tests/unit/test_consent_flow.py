"""Representative authorization and real-time consent through the policy (S8)."""

from sop_agent.data.repository import InMemoryRepository
from sop_agent.domain.dates import DateHint
from sop_agent.domain.enums import (
    AuthorizationStatus,
    CallerRole,
    ClaimStatus,
    ConsentStatus,
    IdentityStatus,
    Phase,
    VerifiedAs,
    VerifyStage,
)
from sop_agent.memory.state import SessionState
from sop_agent.nlu.schema import NLUResult
from sop_agent.sop.directive import ActionKind, EventType, PendingQuestionKind
from sop_agent.sop.policy import PolicyEngine
from tests.builders import new_state, nlu
from tests.policy_helpers import F, consent, engine, no, turn, types, yes


def _david(**extra: object) -> NLUResult:
    return nlu(
        {F.FULL_NAME: "Margaret Chen", F.DOB: "1985-03-15", F.PHONE: "650-521-2836"},
        caller_role=CallerRole.AUTHORIZED_REPRESENTATIVE,
        representative_name="David Chen",
        relationship_to_policyholder="child",
        case_type="healthcare",
        claim_status=ClaimStatus.DENIED,
        date_hint=DateHint(month=1),
        **extra,
    )


def _executor_sent_request(state: SessionState) -> SessionState:
    """Stand-in for ActionExecutor.run(REQUEST_CONSENT) in the policy-only tests."""
    state = state.model_copy(deep=True)
    state.memory.representative.consent_status = ConsentStatus.PENDING
    return state


def _requested(repo: InMemoryRepository) -> tuple[PolicyEngine, SessionState]:
    eng = engine(repo)
    first = turn(eng, repo, new_state(), _david())
    second = turn(eng, repo, first.state, yes())
    return eng, _executor_sent_request(second.state)


def test_a3_matched_representative_is_asked_to_send_consent_request(
    snapshot_repo: InMemoryRepository,
) -> None:
    decision = turn(engine(snapshot_repo), snapshot_repo, new_state(), _david())
    state = decision.state
    assert EventType.IDENTITY_VERIFIED in types(decision)
    assert EventType.VERIFIED not in types(decision)
    assert (state.phase, state.verify_stage) == (Phase.VERIFY_ID, VerifyStage.CONSENT)
    assert state.memory.representative.authorization is AuthorizationStatus.MATCHED
    assert state.memory.identity.status is IdentityStatus.IDENTITY_VERIFIED
    pending = state.pending_question
    assert pending is not None and pending.kind is PendingQuestionKind.CONFIRM_CONSENT_REQUEST
    assert decision.actions == []


def test_a5_yes_plans_the_consent_request_without_claiming_it_was_sent(
    snapshot_repo: InMemoryRepository,
) -> None:
    eng = engine(snapshot_repo)
    state = turn(eng, snapshot_repo, new_state(), _david()).state
    decision = turn(eng, snapshot_repo, state, yes())
    assert [a.kind for a in decision.actions] == [ActionKind.REQUEST_CONSENT]
    assert decision.actions[0].params == {"consent_scenario": "default"}
    assert decision.state.phase is Phase.VERIFY_ID
    assert any("unless CONSENT_REQUESTED" in m for m in decision.directive.must)


def test_a6_default_scenario_pending_then_approved_uses_memory_hints(
    snapshot_repo: InMemoryRepository,
) -> None:
    eng, state = _requested(snapshot_repo)
    waiting = turn(eng, snapshot_repo, state, nlu(), consent("pending"))
    assert waiting.state.phase is Phase.VERIFY_ID  # no transition before consent
    assert EventType.CONSENT_STATUS in types(waiting)
    approved = turn(eng, snapshot_repo, waiting.state, nlu(), consent("approved"))
    kinds = types(approved)
    assert EventType.CONSENT_APPROVED in kinds and EventType.VERIFIED in kinds
    identity = approved.state.memory.identity
    assert (identity.status, identity.verified_as) == (IdentityStatus.VERIFIED, VerifiedAs.REPRESENTATIVE)
    assert approved.state.phase is Phase.PROCESS_CASE  # hints from turn 1 resolved the claim right away
    assert approved.state.memory.selected_case_id == "CL-2048"


def test_a6_timeout_scenario_offers_live_agent_and_never_verifies(snapshot_repo: InMemoryRepository) -> None:
    eng, state = _requested(snapshot_repo)
    for _ in range(3):
        state = turn(eng, snapshot_repo, state, nlu(), consent("pending")).state
    decision = turn(eng, snapshot_repo, state, nlu(), consent("pending", exhausted=True))
    assert EventType.CONSENT_TIMEOUT in types(decision)
    assert decision.state.memory.representative.consent_status is ConsentStatus.TIMEOUT
    assert decision.state.memory.identity.status is IdentityStatus.IDENTITY_VERIFIED
    assert decision.state.phase is Phase.VERIFY_ID
    pending = decision.state.pending_question
    assert pending is not None and pending.kind is PendingQuestionKind.OFFER_LIVE_AGENT
    later = turn(eng, snapshot_repo, decision.state, nlu(), consent("approved"))
    assert later.state.memory.identity.status is IdentityStatus.IDENTITY_VERIFIED  # timeout is final
    assert later.state.memory.representative.consent_status is ConsentStatus.TIMEOUT
    assert later.state.pending_question is not None
    assert later.state.pending_question.kind is PendingQuestionKind.OFFER_LIVE_AGENT  # no new consent request
    assert later.actions == []


def test_a6_declined_scenario(snapshot_repo: InMemoryRepository) -> None:
    eng, state = _requested(snapshot_repo)
    decision = turn(eng, snapshot_repo, state, nlu(), consent("declined"))
    assert EventType.CONSENT_DECLINED in types(decision)
    assert decision.state.phase is Phase.VERIFY_ID
    assert decision.directive.offer_live_agent


def test_a6_missing_observation_while_pending_keeps_waiting(snapshot_repo: InMemoryRepository) -> None:
    eng, state = _requested(snapshot_repo)
    decision = turn(eng, snapshot_repo, state, nlu())
    assert decision.state.phase is Phase.VERIFY_ID
    assert decision.state.memory.representative.consent_status is ConsentStatus.PENDING


def test_a5_caller_declining_the_request_offers_live_agent(snapshot_repo: InMemoryRepository) -> None:
    eng = engine(snapshot_repo)
    state = turn(eng, snapshot_repo, new_state(), _david()).state
    decision = turn(eng, snapshot_repo, state, no())
    assert decision.actions == []
    assert decision.directive.offer_live_agent


def test_a4_unregistered_representative_is_not_authorized_for_good(snapshot_repo: InMemoryRepository) -> None:
    eng = engine(snapshot_repo)
    message = _david().model_copy(update={"representative_name": "Kevin Chen"})
    decision = turn(eng, snapshot_repo, new_state(), message)
    assert EventType.REP_NOT_AUTHORIZED in types(decision)
    assert decision.state.phase is Phase.VERIFY_ID
    assert decision.directive.offer_live_agent
    retry = turn(eng, snapshot_repo, decision.state, nlu(representative_name="David Chen"))
    assert retry.state.memory.representative.authorization is AuthorizationStatus.NOT_AUTHORIZED
    assert EventType.REP_NOT_AUTHORIZED not in types(retry)


def test_a3_missing_representative_details_are_requested(snapshot_repo: InMemoryRepository) -> None:
    eng = engine(snapshot_repo)
    message = _david().model_copy(update={"relationship_to_policyholder": None})
    decision = turn(eng, snapshot_repo, new_state(), message)
    assert decision.state.verify_stage is VerifyStage.AUTHORIZATION
    assert any("relationship" in m for m in decision.directive.must)
    done = turn(eng, snapshot_repo, decision.state, nlu(relationship_to_policyholder="son"))
    assert done.state.verify_stage is VerifyStage.CONSENT


def test_a2_representative_directive_reminds_details_are_the_policyholders(
    snapshot_repo: InMemoryRepository,
) -> None:
    message = nlu(caller_role=CallerRole.AUTHORIZED_REPRESENTATIVE, representative_name="David Chen")
    decision = turn(engine(snapshot_repo), snapshot_repo, new_state(), message)
    assert any("policyholder's, not the caller's" in m for m in decision.directive.must)
