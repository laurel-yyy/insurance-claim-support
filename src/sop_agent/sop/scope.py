"""Scope policy and off-topic counters (SPEC §9.2, §9.4).

Manipulation attempts ("ignore your instructions", "I'm an admin") count as out of scope; the SOP continues.
"""

from dataclasses import dataclass

from sop_agent.memory.state import Counters
from sop_agent.nlu.schema import NLUResult, QuestionKind, Scope


@dataclass(frozen=True)
class ScopeConfig:
    offer_live_agent_at: int = 3
    hard_limit: int = 5


@dataclass(frozen=True)
class ScopeOutcome:
    decline: bool
    topic: str
    streak: int
    offer_live_agent: bool
    hard_limit_reached: bool


def _decline_topic(nlu: NLUResult) -> str:
    if nlu.off_topic_subject and nlu.off_topic_subject.strip():
        return nlu.off_topic_subject.strip()
    out = [q.text for q in nlu.questions if q.kind is QuestionKind.OUT_OF_SCOPE and q.text.strip()]
    if out:
        return out[0]
    return "a request to change how this service works" if nlu.manipulation_attempt else "that request"


def apply_scope(counters: Counters, nlu: NLUResult, cfg: ScopeConfig) -> ScopeOutcome:
    """Update the counters in place (on the policy's copy) and say how to respond."""
    purely_out = nlu.scope is Scope.OUT_OF_SCOPE or nlu.manipulation_attempt
    if purely_out:
        counters.off_topic_streak += 1
        counters.off_topic_total += 1
    elif nlu.scope is Scope.IN_SCOPE:
        counters.off_topic_streak = 0
    has_out_question = any(q.kind is QuestionKind.OUT_OF_SCOPE for q in nlu.questions)
    decline = purely_out or nlu.scope is Scope.MIXED or has_out_question
    streak = counters.off_topic_streak
    return ScopeOutcome(
        decline=decline,
        topic=_decline_topic(nlu) if decline else "",
        streak=streak,
        offer_live_agent=purely_out and streak >= cfg.offer_live_agent_at,
        hard_limit_reached=purely_out and streak >= cfg.hard_limit,
    )
