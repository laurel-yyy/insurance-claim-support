"""Composition root: the only place that builds and wires services from Settings (§3.2)."""

from dataclasses import dataclass
from pathlib import Path

from sop_agent.agent.context import ContextBuilder
from sop_agent.agent.guard import OutputGuard
from sop_agent.agent.observer import Observer
from sop_agent.agent.orchestrator import Agents, Orchestrator
from sop_agent.agent.prompt_loader import load_prompt
from sop_agent.agent.responder import Responder, ResponderPrompts
from sop_agent.agent.sensitive_index import SensitiveIndex
from sop_agent.config import Settings
from sop_agent.data.loaders import DataSummary, load_repository, summarize
from sop_agent.data.repository import InMemoryRepository
from sop_agent.domain.clock import Clock, FixedClock, SystemClock
from sop_agent.llm.anthropic_client import AnthropicClient
from sop_agent.llm.base import LLMClient
from sop_agent.memory.store import InMemorySessionStore
from sop_agent.nlu.extractor import Extractor
from sop_agent.nlu.selector import ClaimSelector
from sop_agent.observability.logging import get_logger
from sop_agent.observability.trace import JsonlTraceWriter, TraceSink
from sop_agent.postprocess.summary import BasicSummaryDrafter
from sop_agent.sop.emotion import EmotionConfig
from sop_agent.sop.handlers.base import PolicyConfig
from sop_agent.sop.phases import PHASES
from sop_agent.sop.policy import PolicyEngine
from sop_agent.sop.scope import ScopeConfig
from sop_agent.sop.verification import VerifyConfig
from sop_agent.tools.consent import ScenarioConsentService
from sop_agent.tools.email import MockOutbox
from sop_agent.tools.executor import ActionExecutor
from sop_agent.tools.handoff import LiveAgentHandoff
from sop_agent.tools.registry import ToolRegistry

EXTRACTOR_PROMPT = "extractor.md"
SELECTOR_PROMPT = "selector.md"
RESPONDER_PROMPT = "responder_system.md"

_log = get_logger(__name__)


@dataclass(frozen=True)
class Prompts:
    extractor: str
    selector: str
    responder: ResponderPrompts


@dataclass(frozen=True)
class Services:
    """Stateful runtime services shared by every session of one app instance."""

    store: InMemorySessionStore
    consent: ScenarioConsentService
    outbox: MockOutbox
    handoff: LiveAgentHandoff
    registry: ToolRegistry
    index: SensitiveIndex


@dataclass(frozen=True)
class Container:
    settings: Settings
    clock: Clock
    repository: InMemoryRepository
    data_summary: DataSummary
    policy: PolicyEngine
    prompts: Prompts
    services: Services

    @classmethod
    def build(cls, settings: Settings) -> "Container":
        clock: Clock = FixedClock(settings.demo_today) if settings.demo_today else SystemClock()
        repository = load_repository(settings.fixtures_dir, settings.language)
        summary = summarize(repository)
        _log.info("fixtures loaded", extra={"fields": summary.model_dump(mode="json")})
        var = settings.var_dir
        services = Services(
            store=InMemorySessionStore(),
            consent=ScenarioConsentService(repository, sms_dir=var / "sms"),
            outbox=MockOutbox(var / "outbox"),
            handoff=LiveAgentHandoff(var / "handoffs"),
            registry=ToolRegistry(repository),
            index=SensitiveIndex(repository),
        )
        return cls(
            settings=settings,
            clock=clock,
            repository=repository,
            data_summary=summary,
            policy=PolicyEngine(repository, policy_config(settings)),
            prompts=load_prompts(),
            services=services,
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

    def make_responder(self, llm: LLMClient) -> Responder:
        s = self.settings
        return Responder(
            llm,
            s.responder_model,
            self.prompts.responder,
            self.services.registry,
            agent_name=s.agent_name,
            company_name=s.company_name,
            effort=s.responder_effort,
            max_rounds=s.tool_loop_max_rounds,
        )

    def make_agents(self, llm: LLMClient) -> Agents:
        return Agents(self.make_extractor(llm), self.make_selector(llm), self.make_responder(llm))

    def make_orchestrator(self, agents: Agents, tracer: TraceSink | None = None) -> Orchestrator:
        """One orchestrator for the server key (M6 adds per-session keys via `agents_for`)."""
        s, services = self.settings, self.services
        executor = ActionExecutor(
            repo=self.repository,
            clock=self.clock,
            consent=services.consent,
            outbox=services.outbox,
            handoff=services.handoff,
            drafter=BasicSummaryDrafter(self.repository, s.company_name),
        )
        return Orchestrator(
            store=services.store,
            policy=self.policy,
            context=ContextBuilder(self.repository, _read_faq(s.faq_path)),
            guard=OutputGuard(services.index),
            executor=executor,
            observer=Observer(self.repository, services.consent),
            agents_for=lambda _session_id: agents,
            tracer=tracer or JsonlTraceWriter(s.var_dir / "traces"),
            clock=self.clock,
            agent_name=s.agent_name,
            company_name=s.company_name,
            default_consent_scenario=s.consent_scenario,
        )


def load_prompts() -> Prompts:
    phases = {spec.phase.value: load_prompt(spec.instructions_file) for spec in PHASES.values()}
    return Prompts(
        extractor=load_prompt(EXTRACTOR_PROMPT),
        selector=load_prompt(SELECTOR_PROMPT),
        responder=ResponderPrompts(system=load_prompt(RESPONDER_PROMPT), phases=phases),
    )


def _read_faq(path: Path) -> str:
    """The general FAQ (§6.7). A missing file is a setup error, reported once at startup."""
    if not path.is_file():
        _log.warning("faq file not found", extra={"fields": {"path": str(path)}})
        return ""
    return path.read_text(encoding="utf-8")


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
