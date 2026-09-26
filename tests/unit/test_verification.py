from datetime import date

from sop_agent.data.repository import ClaimsRepository, InMemoryRepository
from sop_agent.domain.enums import CallerRole, IdentityField, IdentityStatus, VerifiedAs
from sop_agent.domain.models import Policyholder
from sop_agent.memory.state import IdentityState
from sop_agent.sop.directive import EventType
from sop_agent.sop.verification import (
    Ambiguous,
    Failed,
    IdentityCheck,
    NeedMore,
    Verified,
    evaluate_identity,
    is_feasible,
    run_identity_check,
    should_evaluate,
    suggest_fields,
)
from tests.builders import CFG, identity
from tests.helpers import snapshot_repository

# Normalized starter values (P9 = Margaret Chen, P12 = Ma Tian, P13 = Ya Wen Li) and edge case P90.
P9 = {"full_name": "margaret chen", "dob": "1985-03-15", "id_last4": "4472", "phone": "6505212836"}
P9_EMAIL = "margaret@email.com"
P12_DOB = "1964-09-10"
P90 = {"dob": "1972-07-04", "id_last4": "8123", "phone": "4155550190"}


def _check(
    state: IdentityState, repo: ClaimsRepository, role: CallerRole = CallerRole.POLICYHOLDER
) -> IdentityCheck:
    return run_identity_check(state, role, repo, CFG, turn=1)


def _event_types(check: IdentityCheck) -> list[EventType]:
    return [e.type for e in check.events]


def test_v1_three_matching_fields_pass(snapshot_repo: InMemoryRepository) -> None:
    outcome = evaluate_identity(
        identity(full_name=P9["full_name"], dob=P9["dob"], id_last4=P9["id_last4"]), snapshot_repo, CFG
    )
    assert isinstance(outcome, Verified)
    assert outcome.party_id == "P9"
    assert outcome.matched_fields == {IdentityField.FULL_NAME, IdentityField.DOB, IdentityField.ID_LAST4}


def test_v1_pass_sets_verified_status_party_and_event(snapshot_repo: InMemoryRepository) -> None:
    check = _check(identity(full_name=P9["full_name"], dob=P9["dob"], phone=P9["phone"]), snapshot_repo)
    assert check.identity.status is IdentityStatus.VERIFIED
    assert check.identity.verified_as is VerifiedAs.POLICYHOLDER
    assert check.identity.party_id == "P9"
    assert _event_types(check) == [EventType.VERIFIED]


def test_v1_matched_fields_never_appear_in_events(snapshot_repo: InMemoryRepository) -> None:
    check = _check(identity(full_name=P9["full_name"], dob=P9["dob"], phone=P9["phone"]), snapshot_repo)
    assert all(not e.data for e in check.events)


def test_v1_representative_pass_only_confirms_identity(snapshot_repo: InMemoryRepository) -> None:
    state = identity(full_name=P9["full_name"], dob=P9["dob"], phone=P9["phone"])
    check = _check(state, snapshot_repo, CallerRole.AUTHORIZED_REPRESENTATIVE)
    assert check.identity.status is IdentityStatus.IDENTITY_VERIFIED
    assert check.identity.verified_as is None
    assert _event_types(check) == [EventType.IDENTITY_VERIFIED]


def test_v1_unknown_role_is_treated_as_policyholder(snapshot_repo: InMemoryRepository) -> None:
    check = _check(
        identity(full_name=P9["full_name"], dob=P9["dob"], phone=P9["phone"]),
        snapshot_repo,
        CallerRole.UNKNOWN,
    )
    assert check.identity.status is IdentityStatus.VERIFIED


def test_v1_fields_from_different_policyholders_do_not_add_up(snapshot_repo: InMemoryRepository) -> None:
    state = identity(full_name=P9["full_name"], dob=P12_DOB, id_last4=P9["id_last4"])
    assert isinstance(evaluate_identity(state, snapshot_repo, CFG), Failed)


def test_v2_two_fields_need_more_without_counting_a_failure(snapshot_repo: InMemoryRepository) -> None:
    wrong = identity(full_name=P9["full_name"], dob="2000-01-01")
    right = identity(full_name=P9["full_name"], dob=P9["dob"])
    for state in (wrong, right):
        check = _check(state, snapshot_repo)
        assert check.outcome == NeedMore(missing=1)
        assert check.identity.failed_attempts == 0
        assert check.events == []
        assert check.identity.last_evaluated_signature is None


def test_v2_no_reevaluation_without_new_information(snapshot_repo: InMemoryRepository) -> None:
    first = _check(identity(full_name=P9["full_name"], dob=P9["dob"], phone="6505212839"), snapshot_repo)
    assert first.identity.failed_attempts == 1
    assert not should_evaluate(first.identity, CFG)
    again = _check(first.identity, snapshot_repo)
    assert again.outcome is None
    assert again.identity.failed_attempts == 1
    assert again.events == []


def test_v2_new_policy_number_counts_as_new_information(snapshot_repo: InMemoryRepository) -> None:
    first = _check(identity(full_name=P9["full_name"], dob=P9["dob"], phone="6505212839"), snapshot_repo)
    changed = first.identity.model_copy(update={"policy_number": "POL-9921"})
    assert should_evaluate(changed, CFG)


def test_v3_one_wrong_field_fails_and_counts_one_attempt(snapshot_repo: InMemoryRepository) -> None:
    check = _check(identity(full_name=P9["full_name"], dob=P9["dob"], id_last4="0000"), snapshot_repo)
    assert isinstance(check.outcome, Failed)
    assert check.identity.failed_attempts == 1
    assert check.identity.status is IdentityStatus.UNVERIFIED
    assert check.identity.party_id is None
    assert _event_types(check) == [EventType.VERIFICATION_FAILED]
    assert check.events[0].data == {"attempt": 1}


def test_v3_failure_keeps_values_and_correction_then_passes(snapshot_repo: InMemoryRepository) -> None:
    failed = _check(
        identity(full_name=P9["full_name"], dob=P9["dob"], id_last4="0000"), snapshot_repo
    ).identity
    assert failed.valid_values()[IdentityField.ID_LAST4] == "0000"
    corrected = failed.model_copy(deep=True)
    corrected.values[IdentityField.ID_LAST4].value = P9["id_last4"]
    check = _check(corrected, snapshot_repo)
    assert check.identity.status is IdentityStatus.VERIFIED
    assert check.identity.failed_attempts == 1


def test_v3_extra_field_after_failure_can_pass(snapshot_repo: InMemoryRepository) -> None:
    failed = _check(
        identity(full_name=P9["full_name"], dob=P9["dob"], id_last4="0000"), snapshot_repo
    ).identity
    more = identity(full_name=P9["full_name"], dob=P9["dob"], id_last4="0000", email=P9_EMAIL)
    more.failed_attempts = failed.failed_attempts
    more.last_evaluated_signature = failed.last_evaluated_signature
    assert _check(more, snapshot_repo).identity.status is IdentityStatus.VERIFIED


def test_v4_three_consecutive_failures_lock(snapshot_repo: InMemoryRepository) -> None:
    state = identity(full_name=P9["full_name"], dob=P9["dob"], id_last4="0000")
    for attempt, wrong in enumerate(("0000", "1111", "2222"), start=1):
        state = state.model_copy(deep=True)
        state.values[IdentityField.ID_LAST4].value = wrong
        check = _check(state, snapshot_repo)
        state = check.identity
        assert state.failed_attempts == attempt
    assert state.status is IdentityStatus.LOCKED
    assert _event_types(check) == [EventType.VERIFICATION_FAILED, EventType.VERIFICATION_LOCKED]


def test_v4_locked_session_never_verifies_even_with_correct_fields(snapshot_repo: InMemoryRepository) -> None:
    locked = identity(full_name=P9["full_name"], dob=P9["dob"], id_last4=P9["id_last4"])
    locked.status = IdentityStatus.LOCKED
    check = _check(locked, snapshot_repo)
    assert check.outcome is None
    assert check.identity.status is IdentityStatus.LOCKED
    assert not should_evaluate(locked, CFG)


def test_v5_policy_number_narrows_same_name_to_the_right_person(merged_repo: InMemoryRepository) -> None:
    state = identity(
        policy_number="POL-5530", full_name="margaret chen", dob=P90["dob"], id_last4=P90["id_last4"]
    )
    outcome = evaluate_identity(state, merged_repo, CFG)
    assert isinstance(outcome, Verified)
    assert outcome.party_id == "P90"


def test_v1_same_name_does_not_mix_two_people(merged_repo: InMemoryRepository) -> None:
    state = identity(full_name="margaret chen", dob=P90["dob"], id_last4=P9["id_last4"])
    assert isinstance(evaluate_identity(state, merged_repo, CFG), Failed)


def test_v5_nonexistent_policy_number_is_ignored(snapshot_repo: InMemoryRepository) -> None:
    state = identity(
        policy_number="POL-0000", full_name=P9["full_name"], dob=P9["dob"], id_last4=P9["id_last4"]
    )
    outcome = evaluate_identity(state, snapshot_repo, CFG)
    assert isinstance(outcome, Verified)
    assert outcome.party_id == "P9"


def test_v5_someone_elses_policy_number_does_not_pass(snapshot_repo: InMemoryRepository) -> None:
    state = identity(
        policy_number="POL-8836", full_name=P9["full_name"], dob=P9["dob"], id_last4=P9["id_last4"]
    )
    assert isinstance(evaluate_identity(state, snapshot_repo, CFG), Failed)


def test_v5_policy_number_never_counts_toward_matches(snapshot_repo: InMemoryRepository) -> None:
    state = identity(policy_number="POL-9921", full_name=P9["full_name"], dob=P9["dob"])
    assert evaluate_identity(state, snapshot_repo, CFG) == NeedMore(missing=1)
    assert IdentityField.FULL_NAME in state.valid_values()
    assert "policy_number" not in {f.value for f in state.valid_values()}


def test_v6_two_records_reaching_threshold_is_ambiguous_not_a_failure() -> None:
    twin = dict(policy_number="POL-1", dob=date(1980, 1, 1), id_type="ssn_last4", phone="+14155550100")
    repo = InMemoryRepository(
        policyholders=[
            Policyholder(party_id="A", name="Alex Kim", id_last4="1111", email="a@x.com", **twin),
            Policyholder(party_id="B", name="Alex Kim", id_last4="2222", email="b@x.com", **twin),
        ],
        claims=[],
        representatives=[],
        consent_scenarios=[],
        glossary={},
        guideline=snapshot_repository().document_guideline(),
    )
    state = identity(full_name="alex kim", dob="1980-01-01", phone="4155550100")
    check = _check(state, repo)
    assert isinstance(check.outcome, Ambiguous)
    assert check.identity.failed_attempts == 0
    assert check.identity.status is IdentityStatus.UNVERIFIED
    assert check.events == []


def test_alias_name_yaven_li_matches_p13(snapshot_repo: InMemoryRepository) -> None:
    state = identity(full_name="yaven li", dob="1989-12-03", id_last4="5317")
    outcome = evaluate_identity(state, snapshot_repo, CFG)
    assert isinstance(outcome, Verified)
    assert outcome.party_id == "P13"


def test_alias_email_matches(snapshot_repo: InMemoryRepository) -> None:
    state = identity(full_name="ya wen li", dob="1989-12-03", email="yawen.li@example.com")
    outcome = evaluate_identity(state, snapshot_repo, CFG)
    assert isinstance(outcome, Verified)
    assert outcome.matched_fields == {IdentityField.FULL_NAME, IdentityField.DOB, IdentityField.EMAIL}


def test_similar_but_unlisted_name_spelling_does_not_match(snapshot_repo: InMemoryRepository) -> None:
    state = identity(full_name="yawen li", dob="1989-12-03", id_last4="5317")
    outcome = evaluate_identity(state, snapshot_repo, CFG)
    assert isinstance(outcome, Failed)


def test_phone_differing_by_one_digit_does_not_match(snapshot_repo: InMemoryRepository) -> None:
    # P13's phone differs from P9's only in the last digit.
    state = identity(full_name=P9["full_name"], dob=P9["dob"], phone="6505212830")
    assert isinstance(evaluate_identity(state, snapshot_repo, CFG), Failed)


def test_national_id_last4_matches_p12(snapshot_repo: InMemoryRepository) -> None:
    state = identity(full_name="ma tian", dob=P12_DOB, id_last4="6688")
    outcome = evaluate_identity(state, snapshot_repo, CFG)
    assert isinstance(outcome, Verified)
    assert outcome.party_id == "P12"


def test_suggest_fields_follows_order_and_skips_provided_and_declined() -> None:
    state = identity(full_name="margaret chen")
    assert suggest_fields(state) == [
        IdentityField.DOB,
        IdentityField.PHONE,
        IdentityField.EMAIL,
        IdentityField.ID_LAST4,
    ]
    state.declined.add(IdentityField.ID_LAST4)
    state.values.update(identity(dob="1985-03-15").values)
    assert suggest_fields(state) == [IdentityField.PHONE, IdentityField.EMAIL]


def test_v8_feasible_while_enough_fields_remain() -> None:
    state = identity(full_name="margaret chen")
    state.declined |= {IdentityField.ID_LAST4, IdentityField.DOB}
    assert is_feasible(state, CFG)  # name + phone + email still possible


def test_v8_infeasible_after_too_many_declined_fields() -> None:
    state = identity(full_name="margaret chen")
    state.declined |= {IdentityField.ID_LAST4, IdentityField.DOB, IdentityField.PHONE}
    assert not is_feasible(state, CFG)


def test_input_identity_state_is_never_mutated(snapshot_repo: InMemoryRepository) -> None:
    state = identity(full_name=P9["full_name"], dob=P9["dob"], id_last4="0000")
    before = state.model_dump()
    _check(state, snapshot_repo)
    assert state.model_dump() == before
