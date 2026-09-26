"""Composition root: the only place that builds and wires services from Settings (§3.2)."""

from dataclasses import dataclass

from sop_agent.config import Settings
from sop_agent.data.loaders import DataSummary, load_repository, summarize
from sop_agent.data.repository import InMemoryRepository
from sop_agent.domain.clock import Clock, FixedClock, SystemClock
from sop_agent.observability.logging import get_logger
from sop_agent.sop.emotion import EmotionConfig
from sop_agent.sop.handlers.base import PolicyConfig
from sop_agent.sop.policy import PolicyEngine
from sop_agent.sop.scope import ScopeConfig
from sop_agent.sop.verification import VerifyConfig

_log = get_logger(__name__)


@dataclass(frozen=True)
class Container:
    """Holds the long-lived services for one app instance."""

    settings: Settings
    clock: Clock
    repository: InMemoryRepository
    data_summary: DataSummary
    policy: PolicyEngine

    @classmethod
    def build(cls, settings: Settings) -> "Container":
        clock: Clock = FixedClock(settings.demo_today) if settings.demo_today else SystemClock()
        repository = load_repository(settings.fixtures_dir, settings.language)
        summary = summarize(repository)
        _log.info("fixtures loaded", extra={"fields": summary.model_dump(mode="json")})
        policy = PolicyEngine(repository, policy_config(settings))
        return cls(settings=settings, clock=clock, repository=repository, data_summary=summary, policy=policy)


def policy_config(settings: Settings) -> PolicyConfig:
    """SOP thresholds from configuration (§14.1)."""
    return PolicyConfig(
        verify=VerifyConfig(
            min_matches=settings.verify_min_matches, max_failed_attempts=settings.verify_max_failed_attempts
        ),
        scope=ScopeConfig(
            offer_live_agent_at=settings.off_topic_offer_live_agent_at,
            hard_limit=settings.off_topic_hard_limit,
        ),
        emotion=EmotionConfig(persuasion_max=settings.persuasion_max),
    )
