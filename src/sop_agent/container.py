"""Composition root: the only place that builds and wires services from Settings (§3.2)."""

from dataclasses import dataclass

from sop_agent.agent.prompt_loader import load_prompt
from sop_agent.config import Settings
from sop_agent.data.loaders import DataSummary, load_repository, summarize
from sop_agent.data.repository import InMemoryRepository
from sop_agent.domain.clock import Clock, FixedClock, SystemClock
from sop_agent.llm.anthropic_client import AnthropicClient
from sop_agent.llm.base import LLMClient
from sop_agent.nlu.extractor import Extractor
from sop_agent.nlu.selector import ClaimSelector
from sop_agent.observability.logging import get_logger
from sop_agent.sop.emotion import EmotionConfig
from sop_agent.sop.handlers.base import PolicyConfig
from sop_agent.sop.policy import PolicyEngine
from sop_agent.sop.scope import ScopeConfig
from sop_agent.sop.verification import VerifyConfig

EXTRACTOR_PROMPT = "extractor.md"
SELECTOR_PROMPT = "selector.md"

_log = get_logger(__name__)


@dataclass(frozen=True)
class Prompts:
    extractor: str
    selector: str


@dataclass(frozen=True)
class Container:
    """Holds the long-lived services for one app instance."""

    settings: Settings
    clock: Clock
    repository: InMemoryRepository
    data_summary: DataSummary
    policy: PolicyEngine
    prompts: Prompts

    @classmethod
    def build(cls, settings: Settings) -> "Container":
        clock: Clock = FixedClock(settings.demo_today) if settings.demo_today else SystemClock()
        repository = load_repository(settings.fixtures_dir, settings.language)
        summary = summarize(repository)
        _log.info("fixtures loaded", extra={"fields": summary.model_dump(mode="json")})
        policy = PolicyEngine(repository, policy_config(settings))
        prompts = Prompts(extractor=load_prompt(EXTRACTOR_PROMPT), selector=load_prompt(SELECTOR_PROMPT))
        return cls(
            settings=settings,
            clock=clock,
            repository=repository,
            data_summary=summary,
            policy=policy,
            prompts=prompts,
        )

    def make_llm(self, api_key: str | None = None) -> LLMClient:
        """An LLM client for the server key, or for a key a tester entered in the UI (§13.1)."""
        key = api_key
        if key is None and self.settings.anthropic_api_key is not None:
            key = self.settings.anthropic_api_key.get_secret_value()
        return AnthropicClient(api_key=key, timeout_seconds=self.settings.llm_timeout_seconds)

    def make_extractor(self, llm: LLMClient) -> Extractor:
        return Extractor(
            llm, self.settings.extractor_model, self.prompts.extractor, self.repository.document_guideline()
        )

    def make_selector(self, llm: LLMClient) -> ClaimSelector:
        return ClaimSelector(llm, self.settings.extractor_model, self.prompts.selector)


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
