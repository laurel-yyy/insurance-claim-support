"""Emotion strategies, persuasion budget and escalation rules (SPEC §9.3).

Code picks the strategy and decides when to stop persuading; the gate itself is never bypassed.
"""

from dataclasses import dataclass

from sop_agent.domain.enums import EmotionLabel, EscalationReason
from sop_agent.memory.state import Counters
from sop_agent.nlu.schema import DialogAct, NLUResult
from sop_agent.sop.directive import EmotionPlan, EmotionStrategy
from sop_agent.sop.reasons import ReasonKey

NEGATIVE_EMOTIONS = frozenset(
    {
        EmotionLabel.FRUSTRATED,
        EmotionLabel.ANGRY,
        EmotionLabel.ANXIOUS,
        EmotionLabel.SAD,
        EmotionLabel.CONFUSED,
    }
)
RESISTANT_EMOTIONS = frozenset({EmotionLabel.FRUSTRATED, EmotionLabel.ANGRY})
STRONG_INTENSITY = 2


STRATEGY_BY_EMOTION: dict[EmotionLabel, EmotionStrategy] = {
    EmotionLabel.FRUSTRATED: EmotionStrategy.ACKNOWLEDGE_EXPLAIN_OFFER_OPTIONS,
    EmotionLabel.ANGRY: EmotionStrategy.ACKNOWLEDGE_EXPLAIN_OFFER_OPTIONS,
    EmotionLabel.ANXIOUS: EmotionStrategy.REASSURE_EXPLAIN_NEXT,
    EmotionLabel.CONFUSED: EmotionStrategy.SIMPLIFY_ONE_STEP,
    EmotionLabel.SAD: EmotionStrategy.CARE_THEN_GENTLE,
}


@dataclass(frozen=True)
class EmotionConfig:
    persuasion_max: int = 3
    negative_streak_offer_at: int = 3
    abusive_limit: int = 2


def plan_emotion(nlu: NLUResult, gate_reason: ReasonKey | None) -> EmotionPlan | None:
    """§9.3.2: abusive > refusing > the emotion label. Neutral with no refusal needs no plan."""
    if nlu.abusive:
        return EmotionPlan(
            label=nlu.emotion, intensity=nlu.emotion_intensity, strategy=EmotionStrategy.CALM_BOUNDARY
        )
    if DialogAct.REFUSE in nlu.dialog_acts:
        return EmotionPlan(
            label=nlu.emotion,
            intensity=nlu.emotion_intensity,
            strategy=EmotionStrategy.RESPECT_EXPLAIN_ALTERNATIVES,
            reason_key=gate_reason,
        )
    strategy = STRATEGY_BY_EMOTION.get(nlu.emotion)
    if strategy is None or nlu.emotion_intensity <= 0:
        return None
    return EmotionPlan(
        label=nlu.emotion, intensity=nlu.emotion_intensity, strategy=strategy, reason_key=gate_reason
    )


def is_resisting_gate(nlu: NLUResult) -> bool:
    """Refusing, insisting ("just tell me"), or clear frustration at the gate (§9.3.4)."""
    if DialogAct.REFUSE in nlu.dialog_acts or DialogAct.COMPLAIN in nlu.dialog_acts:
        return True
    return nlu.emotion in RESISTANT_EMOTIONS and nlu.emotion_intensity >= STRONG_INTENSITY


def update_emotion_counters(counters: Counters, nlu: NLUResult, progress: bool) -> None:
    if nlu.abusive:
        counters.abusive_count += 1
    strong_negative = nlu.emotion in NEGATIVE_EMOTIONS and nlu.emotion_intensity >= STRONG_INTENSITY
    counters.negative_emotion_streak = (
        0 if progress or not strong_negative else counters.negative_emotion_streak + 1
    )


def update_persuasion(counters: Counters, nlu: NLUResult, at_gate: bool, progress: bool) -> None:
    """Budget counts only at VERIFY_ID gates; any progress resets it."""
    if progress or not at_gate:
        counters.persuasion_attempts = 0
    elif is_resisting_gate(nlu):
        counters.persuasion_attempts += 1


@dataclass(frozen=True)
class EscalationSignals:
    safety_concern: bool
    requests_live_agent: bool
    accepted_offer_reason: EscalationReason | None
    off_topic_hard_limit: bool
    persuasion_exhausted: bool
    abusive_count: int


def escalation_reason(signals: EscalationSignals, cfg: EmotionConfig) -> EscalationReason | None:
    """§9.3.4 priority order. Lockout (rule 3) is decided by the VERIFY_ID handler after evaluation."""
    if signals.safety_concern:
        return EscalationReason.SAFETY
    if signals.requests_live_agent:
        return EscalationReason.CALLER_REQUEST
    if signals.accepted_offer_reason is not None:
        return signals.accepted_offer_reason
    if signals.off_topic_hard_limit:
        return EscalationReason.OFF_TOPIC
    if signals.persuasion_exhausted:
        return EscalationReason.PERSUASION_EXHAUSTED
    if signals.abusive_count >= cfg.abusive_limit:
        return EscalationReason.ABUSE
    return None
