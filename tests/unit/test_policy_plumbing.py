from datetime import date

from sop_agent.config import Settings
from sop_agent.container import Container
from sop_agent.data.repository import InMemoryRepository
from sop_agent.domain.enums import Phase
from sop_agent.memory.merge import mark_deferred_answered
from sop_agent.memory.state import DeferredQuestion
from sop_agent.nlu.schema import Confirmation
from sop_agent.sop.directive import Observations, PendingQuestion, PendingQuestionKind
from sop_agent.sop.handlers.base import PolicyConfig, TurnContext
from tests.builders import new_state, nlu
from tests.helpers import SNAPSHOT_DIR


def _ctx(
    repo: InMemoryRepository, pending: PendingQuestion | None, origin: Phase = Phase.POST_PROCESS
) -> TurnContext:
    return TurnContext(
        nlu=nlu(confirmation=Confirmation.YES),
        observations=Observations(),
        repo=repo,
        cfg=PolicyConfig(),
        turn=5,
        origin_phase=origin,
        pending=pending,
    )


def _pending(phase: Phase) -> PendingQuestion:
    return PendingQuestion(kind=PendingQuestionKind.OFFER_SUMMARY_EMAIL, asked_in_phase=phase, asked_turn=4)


def test_answer_is_consumed_once(snapshot_repo: InMemoryRepository) -> None:
    ctx = _ctx(snapshot_repo, _pending(Phase.POST_PROCESS))
    assert ctx.answer(PendingQuestionKind.OFFER_SUMMARY_EMAIL, Phase.POST_PROCESS) is Confirmation.YES
    assert ctx.answer(PendingQuestionKind.OFFER_SUMMARY_EMAIL, Phase.POST_PROCESS) is None


def test_answer_belongs_only_to_the_asking_phase_and_kind(snapshot_repo: InMemoryRepository) -> None:
    ctx = _ctx(snapshot_repo, _pending(Phase.PROCESS_CASE))
    assert ctx.answer(PendingQuestionKind.OFFER_SUMMARY_EMAIL, Phase.POST_PROCESS) is None
    ctx = _ctx(snapshot_repo, _pending(Phase.POST_PROCESS))
    assert ctx.answer(PendingQuestionKind.ANYTHING_ELSE, Phase.POST_PROCESS) is None
    ctx = _ctx(snapshot_repo, _pending(Phase.POST_PROCESS), origin=Phase.PROCESS_CASE)
    assert ctx.answer(PendingQuestionKind.OFFER_SUMMARY_EMAIL, Phase.POST_PROCESS) is None  # reached by a hop


def test_mark_deferred_answered_returns_a_copy() -> None:
    state = new_state()
    state.memory.deferred_questions.append(DeferredQuestion(text="why denied", turn=1))
    marked = mark_deferred_answered(state)
    assert all(q.answered for q in marked.memory.deferred_questions)
    assert not state.memory.deferred_questions[0].answered


def test_container_wires_policy_thresholds_from_settings() -> None:
    settings = Settings(
        _env_file=None,
        fixtures_dir=SNAPSHOT_DIR,
        demo_today=date(2026, 3, 10),
        verify_min_matches=4,
        off_topic_hard_limit=7,
        persuasion_max=2,
    )
    container = Container.build(settings)
    config = container.policy.config
    assert (config.verify.min_matches, config.scope.hard_limit, config.emotion.persuasion_max) == (4, 7, 2)
