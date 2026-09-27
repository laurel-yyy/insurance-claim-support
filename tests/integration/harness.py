"""End-to-end harness: the real container and orchestrator with one FakeLLMClient (SPEC §15.2).

Requests are routed by what they ask for: extractor requests (NLU wire schema) get the next scripted NLU payload,
selector requests the next selector answer, and responder requests the scripted reply function.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from sop_agent.agent.orchestrator import Orchestrator, TurnResult
from sop_agent.agent.sensitive_index import SensitiveIndex, TokenKind
from sop_agent.config import Settings
from sop_agent.container import Container
from sop_agent.llm.base import LLMRequest, LLMResponse
from sop_agent.llm.fake import FakeLLMClient, ScriptItem
from sop_agent.nlu.wire import NLUWireBase, PathOrNone, SelectorWire
from sop_agent.observability.trace import MemoryTraceSink
from sop_agent.postprocess.summary import EmailContent, SummaryFacts, template_content
from tests.nlu_helpers import wire_payload

SAFE_REPLY = "Thanks, I can help with that."
FACTS_PREFIX = "FACTS:\n"
ResponderFn = Callable[[LLMRequest], ScriptItem]


def _safe(_: LLMRequest) -> ScriptItem:
    return LLMResponse(text=SAFE_REPLY)


def faithful_summary(request: LLMRequest) -> ScriptItem:
    """A well-behaved SummaryWriter: restates the FACTS it was given (so the LLM path is exercised)."""
    facts = SummaryFacts.model_validate_json(str(request.messages[-1].content).removeprefix(FACTS_PREFIX))
    content = template_content(facts).model_copy(update={"greeting": f"Hi {facts.addressee},"})
    return LLMResponse(text=content.model_dump_json(), parsed=content)


@dataclass
class Harness:
    container: Container
    orchestrator: Orchestrator
    llm: FakeLLMClient
    traces: MemoryTraceSink
    nlu_queue: list[dict[str, Any] | Exception] = field(default_factory=list)
    selector_queue: list[SelectorWire] = field(default_factory=list)
    responder: ResponderFn = _safe
    summary: ResponderFn = faithful_summary
    turn_requests: list[list[LLMRequest]] = field(default_factory=list)
    caller_texts: list[str] = field(default_factory=list)
    session_id: str = ""

    def start(self, consent_scenario: str | None = None) -> TurnResult:
        result = self.orchestrator.start_session(consent_scenario)
        self.session_id = result.session_id
        return result

    async def say(self, text: str, **nlu: Any) -> TurnResult:
        """One caller turn with the given NLU fields (the extractor's output is scripted, not guessed)."""
        self.nlu_queue.append(dict(nlu))
        self.caller_texts.append(text)
        before = len(self.llm.requests)
        result = await self.orchestrator.handle_turn(self.session_id, text)
        self.turn_requests.append(self.llm.requests[before:])
        return result

    def route(self, request: LLMRequest) -> ScriptItem:
        model = request.output_model
        if model is not None and issubclass(model, NLUWireBase):
            item = self.nlu_queue.pop(0) if self.nlu_queue else {}
            if isinstance(item, Exception):
                return item
            parsed = model.model_validate(wire_payload(**item))
            return LLMResponse(text=parsed.model_dump_json(), parsed=parsed)
        if model is EmailContent:
            return self.summary(request)
        if model is SelectorWire:
            return (
                self.selector_queue.pop(0)
                if self.selector_queue
                else SelectorWire(case_id="", path=PathOrNone.NONE, confidence=0)
            )
        return self.responder(request)

    def unsaid_record_tokens(
        self, request: LLMRequest, index: SensitiveIndex, caller_texts: list[str]
    ) -> set[TokenKind]:
        """INV-2 scan: record tokens in a request that the caller hasn't said themselves."""
        said = set().union(*(index.keys_in(t) for t in caller_texts)) if caller_texts else set()
        text = request.system + "\n" + "\n".join(str(m.content) for m in request.messages)
        return {h.kind for h in index.find(text) if (h.kind, h.key) not in said}


def build_harness(tmp_path: Path, fixtures_dir: Path) -> Harness:
    settings = Settings(
        _env_file=None,
        fixtures_dir=fixtures_dir,
        demo_today=date(2026, 3, 10),
        var_dir=tmp_path / "var",
        faq_path=Path("data/kb/faq.md"),
    )
    container = Container.build(settings)
    traces = MemoryTraceSink()
    holder: list[Harness] = []
    llm = FakeLLMClient(lambda request: holder[0].route(request))
    orchestrator = container.make_orchestrator(container.make_agents(llm), tracer=traces)
    holder.append(Harness(container=container, orchestrator=orchestrator, llm=llm, traces=traces))
    return holder[0]
