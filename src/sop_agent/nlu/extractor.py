"""Extractor: structured NLU from the LLM, merged with regex pre-extraction.

Never raises for LLM problems: after one retry it falls back to regex-only (degraded) output. Its requests carry
no record data: the system prompt holds only the date, phase, open-question kind, topic names and the last agent
message, and before verification the agent's messages have passed the output guard (INV-2, INV-3).
"""

from sop_agent.agent.prompt_loader import render
from sop_agent.domain.models import DocumentGuideline
from sop_agent.llm.base import LLMClient, LLMError, LLMMessage, LLMRequest, Role
from sop_agent.memory.state import ChatRole, ChatTurn, SessionState
from sop_agent.nlu.convert import degraded_result, to_domain
from sop_agent.nlu.patterns import extract_patterns
from sop_agent.nlu.schema import NLUResult
from sop_agent.nlu.wire import NLUWireBase, build_nlu_wire
from sop_agent.observability.logging import get_logger

HISTORY_TURNS = 6
ATTEMPTS = 2  # One try plus one retry
EXTRACTOR_MAX_TOKENS = 1500
NO_VALUE = "(none)"
MAX_TOPIC_EXAMPLES = 3

_log = get_logger(__name__)


def topic_descriptions(guideline: DocumentGuideline) -> str:
    """FOLLOWUP_TOPICS for the prompt: topic names and a few trigger phrases, never claim data."""
    lines = []
    for topic in guideline.followup_topics:
        examples = ", ".join(f'"{p}"' for p in topic.match_any[:MAX_TOPIC_EXAMPLES])
        detail = f" (e.g. {examples})" if examples else " (missing documents and their substitutes)"
        lines.append(f"- {topic.topic}{detail}")
    return "\n".join(lines) or NO_VALUE


class Extractor:
    def __init__(
        self,
        client: LLMClient,
        model: str,
        prompt_template: str,
        guideline: DocumentGuideline,
        max_tokens: int = EXTRACTOR_MAX_TOKENS,
    ) -> None:
        self._client = client
        self._model = model
        self._template = prompt_template
        self._wire: type[NLUWireBase] = build_nlu_wire(guideline)
        self._topics = topic_descriptions(guideline)
        self._max_tokens = max_tokens

    @property
    def wire_model(self) -> type[NLUWireBase]:
        return self._wire

    async def extract(self, text: str, state: SessionState) -> NLUResult:
        today = state.session_settings.today
        patterns = extract_patterns(text, today)
        request = self.build_request(text, state)
        for attempt in range(1, ATTEMPTS + 1):
            try:
                response = await self._client.create(request)
            except LLMError as exc:
                _log.warning(
                    "extractor call failed",
                    extra={"fields": {"attempt": attempt, "error": type(exc).__name__}},
                )
                continue
            if isinstance(response.parsed, self._wire):
                return to_domain(response.parsed, patterns, today, text)
            _log.warning("extractor returned no structured output", extra={"fields": {"attempt": attempt}})
        return degraded_result(patterns, text)

    def build_request(self, text: str, state: SessionState) -> LLMRequest:
        history = _prior_turns(state.history, text)
        last_agent = next((t.text for t in reversed(history) if t.role is ChatRole.AGENT), NO_VALUE)
        pending = state.pending_question.kind.name if state.pending_question else NO_VALUE
        system = render(
            self._template,
            today=state.session_settings.today.isoformat(),
            phase=state.phase.value,
            pending_question=pending,
            last_agent_message=last_agent,
            followup_topics_with_descriptions=self._topics,
        )
        messages = [_message(t) for t in history] + [LLMMessage(role=Role.USER, content=text)]
        return LLMRequest(
            model=self._model,
            system=system,
            messages=messages,
            output_model=self._wire,
            max_tokens=self._max_tokens,
        )


def _prior_turns(history: list[ChatTurn], text: str) -> list[ChatTurn]:
    """The last turns before the current message, starting with a caller turn (the API's first role)."""
    turns = list(history)
    if turns and turns[-1].role is ChatRole.CALLER and turns[-1].text == text:
        turns = turns[:-1]
    turns = turns[-HISTORY_TURNS:]
    while turns and turns[0].role is not ChatRole.CALLER:
        turns = turns[1:]
    return turns


def _message(turn: ChatTurn) -> LLMMessage:
    return LLMMessage(role=Role.USER if turn.role is ChatRole.CALLER else Role.ASSISTANT, content=turn.text)
