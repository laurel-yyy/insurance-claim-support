"""OutputGuard: the second line of defense on every reply (INV-3).

The first line is data isolation (INV-2). Violations carry only the rule ID and a token kind, never the leaked
text, so neither the regenerate note nor the trace repeats record data.
"""

import re
from dataclasses import dataclass, field
from enum import StrEnum

from sop_agent.agent.sensitive_index import SensitiveIndex, TokenKind, amounts_in, dates_in, phones_in
from sop_agent.domain.enums import IdentityField, Phase
from sop_agent.sop.directive import EventType


class Rule(StrEnum):
    G1 = "G1"  # Leak before verification
    G2 = "G2"  # Cross-policyholder leak
    G3 = "G3"  # PII echo
    G4 = "G4"  # Ungrounded amount or full date
    G5 = "G5"  # False action claim


@dataclass(frozen=True)
class Violation:
    rule: Rule
    kind: str


@dataclass(frozen=True)
class GuardContext:
    verified: bool
    party_id: str | None
    phase: Phase
    caller_texts: list[str]
    caller_identity: dict[IdentityField, str]  # Normalized values the caller gave (for G3)
    grounding_text: str
    tool_text: str = ""
    event_types: frozenset[EventType] = field(default_factory=frozenset)


GROUNDED_PHASES = frozenset({Phase.PROCESS_CASE, Phase.POST_PROCESS})

_APOS = "['\N{RIGHT SINGLE QUOTATION MARK}]"
_WE_HAVE = rf"\b(?:i{_APOS}ve|i have|we{_APOS}ve|we have)\s+(?:just\s+)?"
_ACTION_CLAIMS: tuple[tuple[re.Pattern[str], frozenset[EventType]], ...] = (
    (
        re.compile(rf"{_WE_HAVE}(?:sent|emailed)\b|\b(?:has|have) been (?:sent|emailed)\b", re.I),
        frozenset({EventType.EMAIL_SENT, EventType.CONSENT_REQUESTED}),
    ),
    (
        re.compile(rf"{_WE_HAVE}texted\b|\brequest (?:has been|was) sent\b", re.I),
        frozenset({EventType.CONSENT_REQUESTED}),
    ),
    (
        re.compile(
            rf"{_WE_HAVE}(?:transferred|connected) you\b|\byou(?:{_APOS}ve| have) been transferred\b", re.I
        ),
        frozenset({EventType.ESCALATED}),
    ),
)


class OutputGuard:
    def __init__(self, index: SensitiveIndex) -> None:
        self._index = index

    def check(self, reply: str, ctx: GuardContext) -> list[Violation]:
        violations: list[Violation] = []
        said = set().union(*(self._index.keys_in(t) for t in ctx.caller_texts)) if ctx.caller_texts else set()
        for hit in self._index.find(reply):
            if (hit.kind, hit.key) in said:
                continue
            if not ctx.verified:
                violations.append(Violation(Rule.G1, hit.kind.value))
            elif ctx.party_id not in hit.parties:
                violations.append(Violation(Rule.G2, hit.kind.value))
        violations += _pii_echo(reply, ctx)
        if ctx.phase in GROUNDED_PHASES:
            violations += _ungrounded(reply, ctx)
        violations += _false_action_claims(reply, ctx)
        return _unique(violations)


def _pii_echo(reply: str, ctx: GuardContext) -> list[Violation]:
    """G3: the caller's full DOB, ID last four or full phone number must never be read back."""
    found: list[Violation] = []
    dob = ctx.caller_identity.get(IdentityField.DOB)
    if dob and dob in dates_in(reply)[0]:
        found.append(Violation(Rule.G3, IdentityField.DOB.value))
    last4 = ctx.caller_identity.get(IdentityField.ID_LAST4)
    if last4 and re.search(rf"(?<![\d-]){re.escape(last4)}(?![\d-])", reply):
        found.append(Violation(Rule.G3, IdentityField.ID_LAST4.value))
    phone = ctx.caller_identity.get(IdentityField.PHONE)
    if phone and phone in phones_in(reply):
        found.append(Violation(Rule.G3, IdentityField.PHONE.value))
    return found


def _ungrounded(reply: str, ctx: GuardContext) -> list[Violation]:
    """G4: every amount and full date in the reply must come from grounding, tools or the caller."""
    sources = "\n".join([ctx.grounding_text, ctx.tool_text, *ctx.caller_texts])
    known_amounts = amounts_in(sources)
    known_dates = dates_in(sources)[0]
    found = [Violation(Rule.G4, TokenKind.AMOUNT.value) for a in amounts_in(reply) if a not in known_amounts]
    found += [Violation(Rule.G4, TokenKind.DATE.value) for d in dates_in(reply)[0] if d not in known_dates]
    return found


def _false_action_claims(reply: str, ctx: GuardContext) -> list[Violation]:
    """G5: "I've sent it" / "I've transferred you" only when the matching event happened this turn."""
    return [
        Violation(Rule.G5, "action_claim")
        for pattern, events in _ACTION_CLAIMS
        if pattern.search(reply) and not (events & ctx.event_types)
    ]


def _unique(violations: list[Violation]) -> list[Violation]:
    return list(dict.fromkeys(violations))
