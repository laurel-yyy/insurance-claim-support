"""Scenario assertions (SPEC §15.3). Each returns None when it holds, else a short failure message."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sop_agent.agent.orchestrator import TurnResult
from sop_agent.agent.sensitive_index import SensitiveIndex
from sop_agent.domain.enums import IdentityStatus
from sop_agent.memory.state import SessionState
from sop_agent.sop.directive import EventType


@dataclass
class TurnObservation:
    result: TurnResult
    state: SessionState
    caller_texts: list[str]
    emails_sent: int
    index: SensitiveIndex

    @property
    def reply(self) -> str:
        return self.result.reply

    @property
    def events(self) -> set[str]:
        return {e.type.value for e in self.result.events}


Check = Callable[[TurnObservation, Any], str | None]


def _phase(obs: TurnObservation, expected: str) -> str | None:
    return (
        None if obs.result.phase.value == expected else f"phase {obs.result.phase.value}, expected {expected}"
    )


def _phase_in(obs: TurnObservation, expected: list[str]) -> str | None:
    return (
        None
        if obs.result.phase.value in expected
        else f"phase {obs.result.phase.value}, expected one of {expected}"
    )


def _verify_stage(obs: TurnObservation, expected: str) -> str | None:
    actual = obs.result.verify_stage.value if obs.result.verify_stage else None
    return None if actual == expected else f"verify_stage {actual}, expected {expected}"


def _events_include(obs: TurnObservation, expected: list[str]) -> str | None:
    missing = [e for e in expected if e not in obs.events]
    return f"missing events {missing}" if missing else None


def _events_exclude(obs: TurnObservation, forbidden: list[str]) -> str | None:
    present = [e for e in forbidden if e in obs.events]
    return f"unexpected events {present}" if present else None


def _tools_called_include(obs: TurnObservation, expected: list[str]) -> str | None:
    called = {str(e.data.get("tool")) for e in obs.result.events if e.type is EventType.TOOL_CALLED}
    missing = [t for t in expected if t not in called]
    return f"tools not called {missing}" if missing else None


def _reply_contains_any(obs: TurnObservation, options: list[str]) -> str | None:
    reply = obs.reply.casefold()
    return None if any(o.casefold() in reply for o in options) else f"reply has none of {options}"


def _reply_not_contains(obs: TurnObservation, forbidden: list[str]) -> str | None:
    reply = obs.reply.casefold()
    found = [f for f in forbidden if f.casefold() in reply]
    return f"reply contains {found}" if found else None


def _quick_replies_include(obs: TurnObservation, expected: list[str]) -> str | None:
    missing = [q for q in expected if q not in obs.result.quick_replies]
    return f"missing quick replies {missing} (got {obs.result.quick_replies})" if missing else None


def _email_sent(obs: TurnObservation, expected: bool) -> str | None:
    return (
        None
        if (obs.emails_sent > 0) == expected
        else f"email_sent={obs.emails_sent > 0}, expected {expected}"
    )


def _escalated(obs: TurnObservation, expected: bool) -> str | None:
    return (
        None if obs.result.escalated == expected else f"escalated={obs.result.escalated}, expected {expected}"
    )


def no_record_leak(obs: TurnObservation, expected: bool = True) -> str | None:
    """G1 logic on the reply: before verification, no record token the caller didn't say themselves."""
    if not expected or obs.state.memory.identity.status is IdentityStatus.VERIFIED:
        return None
    said = set().union(*(obs.index.keys_in(t) for t in obs.caller_texts)) if obs.caller_texts else set()
    leaked = sorted({h.kind.value for h in obs.index.find(obs.reply) if (h.kind, h.key) not in said})
    return f"record data before verification: {leaked}" if leaked else None


CHECKS: dict[str, Check] = {
    "phase": _phase,
    "phase_in": _phase_in,
    "verify_stage": _verify_stage,
    "events_include": _events_include,
    "events_exclude": _events_exclude,
    "tools_called_include": _tools_called_include,
    "reply_not_contains": _reply_not_contains,
    "quick_replies_include": _quick_replies_include,
    "email_sent": _email_sent,
    "escalated": _escalated,
    "no_record_leak": no_record_leak,
}


def check_turn(obs: TurnObservation, expect: dict[str, Any]) -> list[str]:
    """Run every assertion in `expect`. `reply_contains_any` may repeat with a suffix (`reply_contains_any_2`)."""
    failures: list[str] = []
    for name, value in expect.items():
        if name.startswith("reply_contains_any"):
            outcome = _reply_contains_any(obs, value)
        elif name in CHECKS:
            outcome = CHECKS[name](obs, value)
        else:
            outcome = f"unknown assertion {name!r}"
        if outcome is not None:
            failures.append(f"{name}: {outcome}")
    return failures
