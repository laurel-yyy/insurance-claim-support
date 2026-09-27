"""Phase transition whitelist (INV-7).

Nothing ever returns to VERIFY_ID (verification stays valid for the session), nothing skips it, and terminal
phases never advance. Only code calls `transition()`; LLM output cannot change the phase (INV-1).
"""

from collections.abc import Mapping
from types import MappingProxyType

from sop_agent.domain.enums import IdentityStatus, Phase
from sop_agent.domain.errors import DomainError
from sop_agent.memory.state import SessionState
from sop_agent.sop.directive import Event, EventType
from sop_agent.sop.phases import TERMINAL_PHASES

MAX_HOPS_PER_TURN = 3

ALLOWED_TRANSITIONS: Mapping[Phase, frozenset[Phase]] = MappingProxyType(
    {
        Phase.VERIFY_ID: frozenset({Phase.RESOLVE_INTENT, Phase.ESCALATED}),
        Phase.RESOLVE_INTENT: frozenset({Phase.PROCESS_CASE, Phase.POST_PROCESS, Phase.ESCALATED}),
        Phase.PROCESS_CASE: frozenset({Phase.RESOLVE_INTENT, Phase.POST_PROCESS, Phase.ESCALATED}),
        Phase.POST_PROCESS: frozenset({Phase.RESOLVE_INTENT, Phase.ENDED, Phase.ESCALATED}),
        Phase.ESCALATED: frozenset(),
        Phase.ENDED: frozenset(),
    }
)


class IllegalTransitionError(DomainError):
    """A transition outside the whitelist was attempted; this is always a code bug, never caller input."""


class HopLimitExceededError(DomainError):
    """More than MAX_HOPS_PER_TURN transitions were attempted in one turn."""


def is_allowed(source: Phase, target: Phase) -> bool:
    return target in ALLOWED_TRANSITIONS[source]


def transition(state: SessionState, target: Phase) -> SessionState:
    """Return a copy of `state` in `target`, with a PHASE_CHANGED event appended.

    Verification must be complete to leave VERIFY_ID for anything but ESCALATED.
    """
    source = state.phase
    if source in TERMINAL_PHASES:
        raise IllegalTransitionError(f"{source} is terminal")
    if not is_allowed(source, target):
        raise IllegalTransitionError(f"{source} -> {target} is not allowed")
    if source is Phase.VERIFY_ID and target is not Phase.ESCALATED and not _verification_complete(state):
        raise IllegalTransitionError("cannot leave VERIFY_ID before verification completes")
    new_state = state.model_copy(deep=True)
    new_state.phase = target
    new_state.counters.turns_in_phase = 0
    new_state.events.append(
        Event(
            type=EventType.PHASE_CHANGED,
            turn=state.counters.turn_index,
            phase=target,
            data={"from": source.value, "to": target.value},
        )
    )
    return new_state


def _verification_complete(state: SessionState) -> bool:
    return state.memory.identity.status is IdentityStatus.VERIFIED


class HopCounter:
    """Counts transitions within one turn so the policy loop can't cycle."""

    def __init__(self, limit: int = MAX_HOPS_PER_TURN) -> None:
        self._limit = limit
        self._used = 0

    @property
    def used(self) -> int:
        return self._used

    def can_hop(self) -> bool:
        return self._used < self._limit

    def hop(self, state: SessionState, target: Phase) -> SessionState:
        if not self.can_hop():
            raise HopLimitExceededError(f"more than {self._limit} transitions in one turn")
        self._used += 1
        return transition(state, target)
