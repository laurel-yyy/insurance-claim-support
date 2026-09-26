"""The summary email consent gate (SPEC §8.4.3, C1-C8, INV-6)."""

from sop_agent.data.repository import InMemoryRepository
from sop_agent.domain.enums import CallerRole, ClaimStatus, Path, Phase, VerifiedAs
from sop_agent.memory.state import SessionState
from sop_agent.nlu.schema import Confirmation, DialogAct, IntentScore, QuestionKind, Scope
from sop_agent.sop.directive import ActionKind, EventType, PendingQuestionKind
from sop_agent.sop.policy import PolicyEngine
from tests.builders import new_state, nlu
from tests.policy_helpers import engine, no, question, turn, types, verified, yes

FILE_ADDRESS = "margaret@email.com"


def _offered(repo: InMemoryRepository, representative: bool = False) -> tuple[PolicyEngine, SessionState]:
    eng = engine(repo)
    state = verified(new_state(), "P9", Phase.PROCESS_CASE)
    state.memory.selected_case_id, state.memory.selected_path = "CL-2048", Path.DENIAL_QUESTION
    state.counters.turns_in_phase = 2
    if representative:
        state.memory.representative.caller_role = CallerRole.AUTHORIZED_REPRESENTATIVE
        state.memory.identity.verified_as = VerifiedAs.REPRESENTATIVE
    decision = turn(eng, repo, state, nlu(dialog_acts=[DialogAct.DONE]))
    assert decision.state.phase is Phase.POST_PROCESS
    return eng, decision.state


def test_c1_offer_targets_file_address_kept_out_of_the_directive(snapshot_repo: InMemoryRepository) -> None:
    _, state = _offered(snapshot_repo)
    pending = state.pending_question
    assert pending is not None and pending.kind is PendingQuestionKind.OFFER_SUMMARY_EMAIL
    assert pending.payload["target"] == FILE_ADDRESS
    assert pending.on_yes is not None and pending.on_yes.params == {"to": FILE_ADDRESS}


def test_c1_directive_never_contains_the_raw_address(snapshot_repo: InMemoryRepository) -> None:
    eng = engine(snapshot_repo)
    state = verified(new_state(), "P9", Phase.PROCESS_CASE)
    state.memory.selected_case_id, state.memory.selected_path = "CL-2048", Path.DENIAL_QUESTION
    decision = turn(eng, snapshot_repo, state, nlu(dialog_acts=[DialogAct.DONE]))
    assert FILE_ADDRESS not in decision.directive.model_dump_json()
    assert decision.directive.quick_replies == ["Send the summary", "No thanks", "Use a different email"]


def test_c2_explicit_yes_sends_to_file_address_and_ends(snapshot_repo: InMemoryRepository) -> None:
    eng, state = _offered(snapshot_repo)
    decision = turn(eng, snapshot_repo, state, yes())
    assert [(a.kind, a.params) for a in decision.actions] == [
        (ActionKind.SEND_SUMMARY_EMAIL, {"to": FILE_ADDRESS})
    ]
    assert decision.state.phase is Phase.ENDED
    assert EventType.SESSION_ENDED in types(decision)
    assert any("If events include EMAIL_SENT" in m for m in decision.directive.must)


def test_c3_explicit_no_skips_and_ends(snapshot_repo: InMemoryRepository) -> None:
    eng, state = _offered(snapshot_repo)
    decision = turn(eng, snapshot_repo, state, no())
    assert decision.actions == []
    assert EventType.EMAIL_SKIPPED in types(decision)
    assert decision.state.phase is Phase.ENDED


def test_c6_two_vague_answers_skip_and_never_send(snapshot_repo: InMemoryRepository) -> None:
    eng, state = _offered(snapshot_repo)
    first = turn(eng, snapshot_repo, state, nlu(confirmation=Confirmation.UNCLEAR))
    assert first.state.phase is Phase.POST_PROCESS
    assert first.state.pending_question is not None and first.state.pending_question.unclear_replies == 1
    second = turn(eng, snapshot_repo, first.state, nlu(confirmation=Confirmation.UNCLEAR))
    assert second.actions == []
    assert second.state.phase is Phase.ENDED
    skipped = next(e for e in second.events if e.type is EventType.EMAIL_SKIPPED)
    assert skipped.data == {"reason": "vague"}
    assert "member portal" in second.directive.fallback_reply


def test_c4_new_address_needs_a_second_confirmation(snapshot_repo: InMemoryRepository) -> None:
    eng, state = _offered(snapshot_repo)
    alt = turn(eng, snapshot_repo, state, nlu(confirmation=Confirmation.YES, summary_email="MChen@Work.com"))
    assert alt.actions == []  # "sure, but send it to X" is not yet consent for X
    pending = alt.state.pending_question
    assert pending is not None and pending.kind is PendingQuestionKind.CONFIRM_ALT_EMAIL
    assert pending.payload["target"] == "mchen@work.com"
    assert "mchen@work.com" in alt.directive.fallback_reply
    sent = turn(eng, snapshot_repo, alt.state, yes())
    assert [a.params for a in sent.actions] == [{"to": "mchen@work.com"}]


def test_c4_no_to_the_new_address_offers_the_file_address_again(snapshot_repo: InMemoryRepository) -> None:
    eng, state = _offered(snapshot_repo)
    alt = turn(eng, snapshot_repo, state, nlu(summary_email="mchen@work.com"))
    back = turn(eng, snapshot_repo, alt.state, no())
    assert back.actions == [] and back.state.phase is Phase.POST_PROCESS
    pending = back.state.pending_question
    assert pending is not None and pending.kind is PendingQuestionKind.OFFER_SUMMARY_EMAIL
    assert pending.payload["target"] == FILE_ADDRESS


def test_c4_invalid_new_address_asks_again(snapshot_repo: InMemoryRepository) -> None:
    eng, state = _offered(snapshot_repo)
    decision = turn(eng, snapshot_repo, state, nlu(summary_email="mchen@work"))
    assert decision.actions == []
    assert "doesn't look complete" in decision.directive.fallback_reply


def test_a8_representative_cannot_switch_addresses(snapshot_repo: InMemoryRepository) -> None:
    eng, state = _offered(snapshot_repo, representative=True)
    assert "Use a different email" not in turn(eng, snapshot_repo, state, nlu()).directive.quick_replies
    decision = turn(
        eng, snapshot_repo, state, nlu(confirmation=Confirmation.YES, summary_email="david@example.com")
    )
    assert decision.actions == []
    pending = decision.state.pending_question
    assert pending is not None and pending.kind is PendingQuestionKind.OFFER_SUMMARY_EMAIL
    assert pending.payload["target"] == FILE_ADDRESS
    assert any("policyholder's file" in m for m in decision.directive.must)


def test_c8_yes_to_a_different_question_never_sends(snapshot_repo: InMemoryRepository) -> None:
    eng, state = _offered(snapshot_repo)
    stale = state.model_copy(deep=True)
    assert stale.pending_question is not None
    stale.pending_question.asked_in_phase = Phase.PROCESS_CASE  # a yes owed to another phase's question
    decision = turn(eng, snapshot_repo, stale, yes())
    assert decision.actions == []
    assert decision.state.phase is Phase.POST_PROCESS


def test_c8_yes_to_a_live_agent_offer_transfers_instead_of_sending(snapshot_repo: InMemoryRepository) -> None:
    eng, state = _offered(snapshot_repo)
    for _ in range(3):
        state = turn(eng, snapshot_repo, state, nlu(scope=Scope.OUT_OF_SCOPE)).state
    assert state.pending_question is not None
    assert state.pending_question.kind is PendingQuestionKind.OFFER_LIVE_AGENT
    decision = turn(eng, snapshot_repo, state, yes())
    assert [a.kind for a in decision.actions] == [ActionKind.TRANSFER_TO_LIVE_AGENT]


def test_c5_question_about_the_email_reasks(snapshot_repo: InMemoryRepository) -> None:
    eng, state = _offered(snapshot_repo)
    decision = turn(
        eng, snapshot_repo, state, nlu(questions=[question("what's in it", QuestionKind.PROCESS)])
    )
    assert decision.actions == [] and decision.state.phase is Phase.POST_PROCESS
    assert any("Summarize the draft" in m for m in decision.directive.must)


def test_c7_new_need_returns_to_resolve_intent_and_consent_is_asked_again(
    snapshot_repo: InMemoryRepository,
) -> None:
    eng, state = _offered(snapshot_repo)
    need = nlu(
        intents=[IntentScore(path=Path.STATUS_INQUIRY, confidence=0.9)],
        case_type="auto",
        claim_status=ClaimStatus.OPEN,  # type + status = 4, the §8.2.2 threshold for auto-resolution
    )
    moved = turn(eng, snapshot_repo, state, need)
    assert moved.state.phase is Phase.PROCESS_CASE
    assert moved.state.memory.selected_case_id == "CL-2102"
    assert moved.actions == []
    back = turn(eng, snapshot_repo, moved.state, nlu(dialog_acts=[DialogAct.DONE]))
    assert back.state.phase is Phase.POST_PROCESS
    assert EventType.EMAIL_OFFERED in types(back)
    assert back.actions == []
