from sop_agent.domain.dates import DateHint
from sop_agent.domain.enums import (
    CallerRole,
    ClaimStatus,
    EmotionLabel,
    IdentityField,
    IdentityStatus,
    Path,
    Phase,
)
from sop_agent.memory.merge import describe_hints, merge
from sop_agent.memory.state import EMOTION_HISTORY_LIMIT
from sop_agent.nlu.schema import IntentScore
from sop_agent.sop.directive import Event, EventType
from tests.builders import new_state, nlu

F = IdentityField


def _types(events: list[Event]) -> list[EventType]:
    return [e.type for e in events]


def test_identity_values_are_normalized_before_storing() -> None:
    state, events = merge(
        new_state(), nlu({F.FULL_NAME: "Chen, Margaret", F.PHONE: "(650) 521-2836"}), turn=1
    )
    values = state.memory.identity.valid_values()
    assert values == {F.FULL_NAME: "margaret chen", F.PHONE: "6505212836"}
    assert _types(events) == [EventType.IDENTITY_FIELD_CAPTURED] * 2
    assert {e.data["field"] for e in events} == {"full_name", "phone"}


def test_identity_correction_replaces_the_old_value() -> None:
    state, _ = merge(new_state(), nlu({F.DOB: "1985-03-16"}), turn=1)
    state, _ = merge(state, nlu({F.DOB: "March 15, 1985"}), turn=2)
    stored = state.memory.identity.values[F.DOB]
    assert (stored.value, stored.turn) == ("1985-03-15", 2)


def test_v7_invalid_format_is_not_written_and_emits_event() -> None:
    state, _ = merge(new_state(), nlu({F.ID_LAST4: "4472"}), turn=1)
    state, events = merge(state, nlu({F.ID_LAST4: "47"}), turn=2)
    assert state.memory.identity.values[F.ID_LAST4].value == "4472"
    assert _types(events) == [EventType.IDENTITY_FIELD_INVALID]
    assert events[0].data == {"field": "id_last4"}


def test_events_never_carry_raw_identity_values() -> None:
    _, events = merge(new_state(), nlu({F.ID_LAST4: "4472", F.DOB: "1985-03-15", F.PHONE: "12"}), turn=1)
    rendered = " ".join(str(e.model_dump()) for e in events)
    assert "4472" not in rendered and "1985" not in rendered


def test_policy_number_is_normalized_and_replaced() -> None:
    state, _ = merge(new_state(), nlu(policy_number="pol-9921"), turn=1)
    state, _ = merge(state, nlu(policy_number="POL-1044"), turn=2)
    assert state.memory.identity.policy_number == "POL-1044"


def test_v8_declined_field_is_recorded_once() -> None:
    state, events = merge(new_state(), nlu(declined_fields=[F.ID_LAST4]), turn=1)
    assert state.memory.identity.declined == {F.ID_LAST4}
    assert _types(events) == [EventType.IDENTITY_FIELD_DECLINED]
    _, again = merge(state, nlu(declined_fields=[F.ID_LAST4]), turn=2)
    assert again == []


def test_v8_volunteering_a_declined_field_removes_it_from_declined() -> None:
    state, _ = merge(new_state(), nlu(declined_fields=[F.ID_LAST4]), turn=1)
    state, _ = merge(state, nlu({F.ID_LAST4: "4472"}), turn=2)
    assert state.memory.identity.declined == set()
    assert F.ID_LAST4 in state.memory.identity.values


def test_identity_is_frozen_after_verification() -> None:
    state = new_state()
    state.memory.identity.status = IdentityStatus.VERIFIED
    merged, events = merge(state, nlu({F.DOB: "1990-01-01"}), turn=3)
    assert merged.memory.identity.values == {}
    assert events == []


def test_hints_are_stored_in_any_phase_with_hint_event() -> None:
    message = nlu(case_type="Healthcare", claim_status=ClaimStatus.DENIED, date_hint=DateHint(month=1))
    state, events = merge(new_state(), message, turn=1)
    hints = state.memory.case_hints
    assert (hints.case_type, hints.status, hints.date_hint) == (
        "healthcare",
        ClaimStatus.DENIED,
        DateHint(month=1),
    )
    assert _types(events) == [EventType.HINT_STORED]
    assert events[0].data == {"kinds": ["case_type", "status", "date"]}
    assert hints.raw_mentions == ["denied healthcare claim, January"]


def test_hints_non_empty_values_replace_and_empty_values_keep() -> None:
    state, _ = merge(new_state(), nlu(case_type="dental", case_id="cl-2048"), turn=1)
    state, _ = merge(state, nlu(case_type="auto", case_id=""), turn=2)
    assert state.memory.case_hints.case_type == "auto"
    assert state.memory.case_hints.case_id == "CL-2048"


def test_keywords_append_without_duplicates() -> None:
    state, _ = merge(new_state(), nlu(description_keywords=["Pathology report", "office note"]), turn=1)
    state, events = merge(state, nlu(description_keywords=["pathology report", "x-ray"]), turn=2)
    assert state.memory.case_hints.keywords == ["pathology report", "office note", "x-ray"]
    assert events[0].data == {"kinds": ["keywords"]}


def test_no_hints_means_no_hint_event() -> None:
    _, events = merge(new_state(), nlu(description_keywords=["  "]), turn=1)
    assert events == []


def test_describe_hints_uses_only_stated_parts() -> None:
    assert describe_hints(nlu(date_hint=DateHint(year=2025, month=11, day=3))) == "November 3 2025"
    assert describe_hints(nlu(case_id="cl-1")) == "CL-1"
    assert describe_hints(nlu()) == ""


def test_intents_keep_highest_confidence_and_latest_turn() -> None:
    first = nlu(intents=[IntentScore(path=Path.DENIAL_QUESTION, confidence=0.8)])
    second = nlu(intents=[IntentScore(path=Path.DENIAL_QUESTION, confidence=0.4)])
    state, _ = merge(new_state(), first, turn=1)
    state, _ = merge(state, second, turn=3)
    candidate = state.memory.intent_candidates[Path.DENIAL_QUESTION]
    assert (candidate.confidence, candidate.last_turn) == (0.8, 3)


def test_claim_question_before_verification_is_deferred() -> None:
    message = nlu(
        questions=["why was my claim denied"],
        intents=[IntentScore(path=Path.DENIAL_QUESTION, confidence=0.8)],
    )
    state, events = merge(new_state(), message, turn=2)
    [deferred] = state.memory.deferred_questions
    assert (deferred.text, deferred.path, deferred.answered) == (
        "why was my claim denied",
        Path.DENIAL_QUESTION,
        False,
    )
    assert _types(events) == [EventType.QUESTION_DEFERRED]
    again, _ = merge(state, message, turn=3)
    assert len(again.memory.deferred_questions) == 1


def test_general_question_is_not_deferred() -> None:
    message = nlu(
        questions=["what is an EOB"],
        intents=[IntentScore(path=Path.GENERAL_INSURANCE_QUESTION, confidence=0.9)],
    )
    state, _ = merge(new_state(), message, turn=1)
    assert state.memory.deferred_questions == []


def test_low_confidence_claim_intent_does_not_defer() -> None:
    message = nlu(questions=["hmm"], intents=[IntentScore(path=Path.STATUS_INQUIRY, confidence=0.3)])
    state, _ = merge(new_state(), message, turn=1)
    assert state.memory.deferred_questions == []


def test_claim_question_in_process_case_is_not_deferred() -> None:
    state = new_state(Phase.PROCESS_CASE)
    state.memory.selected_case_id = "CL-X"
    message = nlu(questions=["why denied"], intents=[IntentScore(path=Path.DENIAL_QUESTION, confidence=0.9)])
    merged, _ = merge(state, message, turn=5)
    assert merged.memory.deferred_questions == []


def test_claim_question_in_resolve_intent_without_selection_is_deferred() -> None:
    message = nlu(questions=["where is it"], intents=[IntentScore(path=Path.STATUS_INQUIRY, confidence=0.9)])
    merged, _ = merge(new_state(Phase.RESOLVE_INTENT), message, turn=5)
    assert [q.text for q in merged.memory.deferred_questions] == ["where is it"]


def test_a1_caller_role_is_never_downgraded() -> None:
    state, _ = merge(new_state(), nlu(caller_role=CallerRole.AUTHORIZED_REPRESENTATIVE), turn=1)
    for role in (CallerRole.POLICYHOLDER, CallerRole.UNKNOWN):
        state, _ = merge(state, nlu(caller_role=role), turn=2)
        assert state.memory.representative.caller_role is CallerRole.AUTHORIZED_REPRESENTATIVE


def test_a1_policyholder_role_is_set_from_unknown() -> None:
    state, _ = merge(new_state(), nlu(caller_role=CallerRole.POLICYHOLDER), turn=1)
    assert state.memory.representative.caller_role is CallerRole.POLICYHOLDER


def test_representative_details_non_empty_values_replace() -> None:
    state, _ = merge(
        new_state(), nlu(representative_name="David Chen", relationship_to_policyholder="child"), turn=1
    )
    state, _ = merge(state, nlu(representative_name="", relationship_to_policyholder="son"), turn=2)
    rep = state.memory.representative
    assert (rep.representative_name, rep.relationship) == ("David Chen", "son")


def test_a2_representatives_own_name_is_not_stored_as_identity() -> None:
    message = nlu(
        {F.FULL_NAME: "David Chen", F.DOB: "1985-03-15"},
        caller_role=CallerRole.AUTHORIZED_REPRESENTATIVE,
        representative_name="David Chen",
    )
    state, _ = merge(new_state(), message, turn=1)
    assert set(state.memory.identity.values) == {F.DOB}


def test_a2_earlier_full_name_is_removed_when_it_turns_out_to_be_the_representative() -> None:
    state, _ = merge(new_state(), nlu({F.FULL_NAME: "David Chen"}), turn=1)
    assert F.FULL_NAME in state.memory.identity.values
    state, _ = merge(
        state, nlu(caller_role=CallerRole.AUTHORIZED_REPRESENTATIVE, representative_name="david chen"), turn=2
    )
    assert F.FULL_NAME not in state.memory.identity.values


def test_a2_policyholder_name_in_a_representative_call_is_kept() -> None:
    message = nlu(
        {F.FULL_NAME: "Margaret Chen"},
        caller_role=CallerRole.AUTHORIZED_REPRESENTATIVE,
        representative_name="David Chen",
    )
    state, _ = merge(new_state(), message, turn=1)
    assert state.memory.identity.values[F.FULL_NAME].value == "margaret chen"


def test_unavailable_documents_append() -> None:
    state, _ = merge(new_state(), nlu(unavailable_documents=["Pathology report"]), turn=1)
    state, _ = merge(state, nlu(unavailable_documents=["office note", "pathology report"]), turn=2)
    assert state.memory.unavailable_documents == {"pathology report", "office note"}


def test_emotion_history_keeps_only_the_last_ten() -> None:
    state = new_state()
    for turn in range(1, 13):
        state, _ = merge(state, nlu(emotion=EmotionLabel.FRUSTRATED, emotion_intensity=2), turn=turn)
    history = state.memory.emotion_history
    assert len(history) == EMOTION_HISTORY_LIMIT
    assert [r.turn for r in history] == list(range(3, 13))


def test_merge_appends_events_to_timeline_and_never_mutates_input() -> None:
    state = new_state()
    before = state.model_dump()
    merged, events = merge(state, nlu({F.DOB: "1985-03-15"}, case_type="dental"), turn=1)
    assert state.model_dump() == before
    assert merged.events == events
    assert merged.phase is state.phase
    assert merged.memory.identity.status is IdentityStatus.UNVERIFIED
