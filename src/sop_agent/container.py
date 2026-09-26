"""Composition root: the only place that builds and wires services from Settings (§3.2)."""

from dataclasses import dataclass

from sop_agent.config import Settings
from sop_agent.data.loaders import DataSummary, load_repository, summarize
from sop_agent.data.repository import InMemoryRepository
from sop_agent.domain.clock import Clock, FixedClock, SystemClock
from sop_agent.observability.logging import get_logger

_log = get_logger(__name__)


@dataclass(frozen=True)
class Container:
    """Holds the long-lived services for one app instance."""

    settings: Settings
    clock: Clock
    repository: InMemoryRepository
    data_summary: DataSummary

    @classmethod
    def build(cls, settings: Settings) -> "Container":
        clock: Clock = FixedClock(settings.demo_today) if settings.demo_today else SystemClock()
        repository = load_repository(settings.fixtures_dir, settings.language)
        summary = summarize(repository)
        _log.info("fixtures loaded", extra={"fields": summary.model_dump(mode="json")})
        return cls(settings=settings, clock=clock, repository=repository, data_summary=summary)
