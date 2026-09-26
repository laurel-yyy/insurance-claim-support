import pytest

from sop_agent.domain.enums import EmotionLabel, EscalationReason
from sop_agent.memory.state import Counters
from sop_agent.nlu.schema import DialogAct, Question, QuestionKind, Scope
from sop_agent.sop.directive import EmotionStrategy
from sop_agent.sop.emotion import (
    EmotionConfig,
    EscalationSignals,
    escalation_reason,
    is_resisting_gate,
    plan_emotion,
    update_emotion_counters,
    update_persuasion,
)
from sop_agent.sop.reasons import REASONS, ReasonKey
from sop_agent.sop.scope import ScopeConfig, apply_scope
from tests.builders import nlu

CFG = ScopeConfig()


def test_out_of_scope_increments_streak_and_total_and_declines() -> None:
    counters = Counters()
    outcome = apply_scope(
        counters, nlu(scope=Scope.OUT_OF_SCOPE, off_topic_subject="reinforcement learning"), CFG
    )
    assert (counters.off_topic_streak, counters.off_topic_total) == (1, 1)
    assert outcome.decline and outcome.topic == "reinforcement learning"
    assert not outcome.offer_live_agent


def test_in_scope_resets_streak_but_keeps_total() -> None:
    counters = Counters(off_topic_streak=2, off_topic_total=2)
    outcome = apply_scope(counters, nlu(scope=Scope.IN_SCOPE), CFG)
    assert (counters.off_topic_streak, counters.off_topic_total) == (0, 2)
    assert not outcome.decline


def test_mixed_turn_declines_without_changing_counters() -> None:
    counters = Counters(off_topic_streak=2)
    message = nlu(scope=Scope.MIXED, questions=[Question(text="what is RL", kind=QuestionKind.OUT_OF_SCOPE)])
    outcome = apply_scope(counters, message, CFG)
    assert counters.off_topic_streak == 2
    assert outcome.decline and outcome.topic == "what is RL"


def test_manipulation_counts_as_out_of_scope() -> None:
    counters = Counters()
    outcome = apply_scope(counters, nlu(scope=Scope.IN_SCOPE, manipulation_attempt=True), CFG)
    assert counters.off_topic_streak == 1 and outcome.decline


@pytest.mark.parametrize(
    ("streak", "offer", "hard"), [(2, False, False), (3, True, False), (4, True, False), (5, True, True)]
)
def test_off_topic_thresholds(streak: int, offer: bool, hard: bool) -> None:
    counters = Counters(off_topic_streak=streak - 1)
    outcome = apply_scope(counters, nlu(scope=Scope.OUT_OF_SCOPE), CFG)
    assert (outcome.offer_live_agent, outcome.hard_limit_reached) == (offer, hard)


@pytest.mark.parametrize(
    ("label", "strategy"),
    [
        (EmotionLabel.FRUSTRATED, EmotionStrategy.ACKNOWLEDGE_EXPLAIN_OFFER_OPTIONS),
        (EmotionLabel.ANGRY, EmotionStrategy.ACKNOWLEDGE_EXPLAIN_OFFER_OPTIONS),
        (EmotionLabel.ANXIOUS, EmotionStrategy.REASSURE_EXPLAIN_NEXT),
        (EmotionLabel.CONFUSED, EmotionStrategy.SIMPLIFY_ONE_STEP),
        (EmotionLabel.SAD, EmotionStrategy.CARE_THEN_GENTLE),
    ],
)
def test_strategy_per_emotion(label: EmotionLabel, strategy: EmotionStrategy) -> None:
    plan = plan_emotion(nlu(emotion=label, emotion_intensity=2), ReasonKey.IDENTITY_VERIFICATION)
    assert plan is not None
    assert (plan.strategy, plan.reason_key) == (strategy, ReasonKey.IDENTITY_VERIFICATION)


def test_neutral_needs_no_plan_and_refusal_and_abuse_take_priority() -> None:
    assert plan_emotion(nlu(), None) is None
    refusing = plan_emotion(
        nlu(dialog_acts=[DialogAct.REFUSE], emotion=EmotionLabel.FRUSTRATED, emotion_intensity=2), None
    )
    assert refusing is not None and refusing.strategy is EmotionStrategy.RESPECT_EXPLAIN_ALTERNATIVES
    abusive = plan_emotion(nlu(abusive=True, dialog_acts=[DialogAct.REFUSE]), None)
    assert abusive is not None and abusive.strategy is EmotionStrategy.CALM_BOUNDARY


def test_resistance_detection() -> None:
    assert is_resisting_gate(nlu(dialog_acts=[DialogAct.REFUSE]))
    assert is_resisting_gate(nlu(dialog_acts=[DialogAct.COMPLAIN]))
    assert is_resisting_gate(nlu(emotion=EmotionLabel.ANGRY, emotion_intensity=2))
    assert not is_resisting_gate(nlu(emotion=EmotionLabel.FRUSTRATED, emotion_intensity=1))
    assert not is_resisting_gate(nlu(dialog_acts=[DialogAct.DENY]))


def test_persuasion_counts_only_at_gate_and_resets_on_progress() -> None:
    counters = Counters()
    refuse = nlu(dialog_acts=[DialogAct.REFUSE])
    update_persuasion(counters, refuse, at_gate=True, progress=False)
    update_persuasion(counters, refuse, at_gate=True, progress=False)
    assert counters.persuasion_attempts == 2
    update_persuasion(counters, refuse, at_gate=True, progress=True)
    assert counters.persuasion_attempts == 0
    update_persuasion(counters, refuse, at_gate=False, progress=False)
    assert counters.persuasion_attempts == 0


def test_negative_emotion_streak_and_abuse_counter() -> None:
    counters = Counters()
    angry = nlu(emotion=EmotionLabel.ANGRY, emotion_intensity=2, abusive=True)
    update_emotion_counters(counters, angry, progress=False)
    update_emotion_counters(counters, angry, progress=False)
    assert (counters.negative_emotion_streak, counters.abusive_count) == (2, 2)
    update_emotion_counters(counters, nlu(emotion=EmotionLabel.ANGRY, emotion_intensity=2), progress=True)
    assert counters.negative_emotion_streak == 0


def _signals(**overrides: object) -> EscalationSignals:
    base: dict[str, object] = dict(
        safety_concern=False,
        requests_live_agent=False,
        accepted_offer_reason=None,
        off_topic_hard_limit=False,
        persuasion_exhausted=False,
        abusive_count=0,
    )
    base.update(overrides)
    return EscalationSignals(**base)  # type: ignore[arg-type]


def test_escalation_priority_order() -> None:
    cfg = EmotionConfig()
    everything = _signals(
        safety_concern=True,
        requests_live_agent=True,
        off_topic_hard_limit=True,
        persuasion_exhausted=True,
        abusive_count=5,
    )
    assert escalation_reason(everything, cfg) is EscalationReason.SAFETY
    assert (
        escalation_reason(_signals(requests_live_agent=True, abusive_count=5), cfg)
        is EscalationReason.CALLER_REQUEST
    )
    assert (
        escalation_reason(_signals(off_topic_hard_limit=True, persuasion_exhausted=True), cfg)
        is EscalationReason.OFF_TOPIC
    )
    assert (
        escalation_reason(_signals(persuasion_exhausted=True, abusive_count=2), cfg)
        is EscalationReason.PERSUASION_EXHAUSTED
    )
    assert escalation_reason(_signals(abusive_count=2), cfg) is EscalationReason.ABUSE
    assert escalation_reason(_signals(abusive_count=1), cfg) is None


def test_every_reason_key_has_text() -> None:
    assert set(REASONS) == set(ReasonKey)
    assert all(text.strip() for text in REASONS.values())
