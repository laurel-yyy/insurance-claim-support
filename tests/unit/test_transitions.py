import pytest

from sop_agent.domain.enums import IdentityStatus, Phase
from sop_agent.memory.state import SessionState
from sop_agent.sop.directive import EventType
from sop_agent.sop.phases import TERMINAL_PHASES
from sop_agent.sop.transitions import (
    ALLOWED_TRANSITIONS,
    MAX_HOPS_PER_TURN,
    HopCounter,
    HopLimitExceededError,
    IllegalTransitionError,
    transition,
)
from tests.builders import new_state

WHITELIST = [(source, target) for source, targets in ALLOWED_TRANSITIONS.items() for target in targets]
NON_TERMINAL = [p for p in Phase if p not in TERMINAL_PHASES]


def _verified(phase: Phase = Phase.VERIFY_ID) -> SessionState:
    state = new_state(phase)
    state.memory.identity.status = IdentityStatus.VERIFIED
    return state


def test_inv7_whitelist_matches_the_expected_table() -> None:
    assert set(WHITELIST) == {
        (Phase.VERIFY_ID, Phase.RESOLVE_INTENT),
        (Phase.VERIFY_ID, Phase.ESCALATED),
        (Phase.RESOLVE_INTENT, Phase.PROCESS_CASE),
        (Phase.RESOLVE_INTENT, Phase.POST_PROCESS),
        (Phase.RESOLVE_INTENT, Phase.ESCALATED),
        (Phase.PROCESS_CASE, Phase.RESOLVE_INTENT),
        (Phase.PROCESS_CASE, Phase.POST_PROCESS),
        (Phase.PROCESS_CASE, Phase.ESCALATED),
        (Phase.POST_PROCESS, Phase.RESOLVE_INTENT),
        (Phase.POST_PROCESS, Phase.ENDED),
        (Phase.POST_PROCESS, Phase.ESCALATED),
    }


@pytest.mark.parametrize(("source", "target"), WHITELIST)
def test_inv7_whitelisted_transitions_are_allowed(source: Phase, target: Phase) -> None:
    moved = transition(_verified(source), target)
    assert moved.phase is target


@pytest.mark.parametrize("source", list(Phase))
def test_inv7_nothing_returns_to_verify_id(source: Phase) -> None:
    with pytest.raises(IllegalTransitionError):
        transition(_verified(source), Phase.VERIFY_ID)


@pytest.mark.parametrize("target", [Phase.PROCESS_CASE, Phase.POST_PROCESS, Phase.ENDED])
def test_inv7_verify_id_cannot_be_skipped(target: Phase) -> None:
    with pytest.raises(IllegalTransitionError):
        transition(_verified(), target)


@pytest.mark.parametrize(
    "status", [IdentityStatus.UNVERIFIED, IdentityStatus.IDENTITY_VERIFIED, IdentityStatus.LOCKED]
)
def test_inv7_leaving_verify_id_requires_completed_verification(status: IdentityStatus) -> None:
    state = new_state()
    state.memory.identity.status = status
    with pytest.raises(IllegalTransitionError):
        transition(state, Phase.RESOLVE_INTENT)


def test_inv7_unverified_caller_can_still_escalate() -> None:
    assert transition(new_state(), Phase.ESCALATED).phase is Phase.ESCALATED


@pytest.mark.parametrize("source", sorted(TERMINAL_PHASES))
@pytest.mark.parametrize("target", list(Phase))
def test_inv7_terminal_phases_never_advance(source: Phase, target: Phase) -> None:
    with pytest.raises(IllegalTransitionError):
        transition(_verified(source), target)


@pytest.mark.parametrize("source", NON_TERMINAL)
def test_inv7_every_non_terminal_phase_can_escalate(source: Phase) -> None:
    assert transition(_verified(source), Phase.ESCALATED).phase is Phase.ESCALATED


def test_transition_appends_phase_changed_and_resets_phase_counter() -> None:
    state = _verified()
    state.counters.turn_index = 4
    state.counters.turns_in_phase = 3
    moved = transition(state, Phase.RESOLVE_INTENT)
    event = moved.events[-1]
    assert event.type is EventType.PHASE_CHANGED
    assert event.turn == 4
    assert event.data == {"from": "VERIFY_ID", "to": "RESOLVE_INTENT"}
    assert moved.counters.turns_in_phase == 0


def test_transition_never_mutates_the_input_state() -> None:
    state = _verified()
    before = state.model_dump()
    transition(state, Phase.RESOLVE_INTENT)
    assert state.model_dump() == before


def test_at_most_three_transitions_per_turn() -> None:
    hops = HopCounter()
    state = _verified()
    for target in (Phase.RESOLVE_INTENT, Phase.PROCESS_CASE, Phase.POST_PROCESS):
        state = hops.hop(state, target)
    assert hops.used == MAX_HOPS_PER_TURN == 3
    assert not hops.can_hop()
    with pytest.raises(HopLimitExceededError):
        hops.hop(state, Phase.ENDED)
    assert state.phase is Phase.POST_PROCESS
