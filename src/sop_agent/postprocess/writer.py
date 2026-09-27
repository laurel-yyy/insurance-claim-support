"""SummaryWriter: rephrases SummaryFacts with structured output.

The draft is checked with the OutputGuard against the facts alone: every amount and date must come from the
facts (G4), nothing may belong to another policyholder (G2), and the caller's DOB, ID digits or phone may not
appear (G3). Any LLM failure or violation falls back to the deterministic template. Never raises.
"""

import time

from sop_agent.agent.guard import GuardContext, OutputGuard
from sop_agent.data.repository import InMemoryRepository
from sop_agent.domain.enums import Phase
from sop_agent.llm.base import LLMClient, LLMError, LLMMessage, LLMRequest, Role
from sop_agent.memory.state import DraftSource, EmailDraft, SessionState
from sop_agent.observability.logging import get_logger
from sop_agent.postprocess.summary import (
    EmailContent,
    SummaryFacts,
    build_facts,
    draft_from,
    template_content,
)

SUMMARY_MAX_TOKENS = 2000

_log = get_logger(__name__)


class SummaryWriter:
    def __init__(
        self,
        client: LLMClient,
        model: str,
        prompt: str,
        repo: InMemoryRepository,
        guard: OutputGuard,
        company: str,
        max_tokens: int = SUMMARY_MAX_TOKENS,
    ) -> None:
        self._client = client
        self._model = model
        self._prompt = prompt
        self._repo = repo
        self._guard = guard
        self._company = company
        self._max_tokens = max_tokens

    async def draft(self, state: SessionState, to: str) -> EmailDraft:
        facts = build_facts(state, self._repo, self._company)
        content = await self._write(facts)
        if content is not None and not self._violations(content, facts, state):
            return draft_from(content, facts, to, DraftSource.LLM)
        return draft_from(template_content(facts), facts, to, DraftSource.TEMPLATE)

    def build_request(self, facts: SummaryFacts) -> LLMRequest:
        return LLMRequest(
            model=self._model,
            system=self._prompt,
            messages=[LLMMessage(role=Role.USER, content=f"FACTS:\n{facts.as_json()}")],
            output_model=EmailContent,
            max_tokens=self._max_tokens,
            effort="low",
        )

    async def _write(self, facts: SummaryFacts) -> EmailContent | None:
        started = time.perf_counter()
        try:
            response = await self._client.create(self.build_request(facts))
        except LLMError as exc:
            _log.warning("summary writer failed", extra={"fields": {"error": type(exc).__name__}})
            return None
        _log.info(
            "summary written", extra={"fields": {"latency_ms": int((time.perf_counter() - started) * 1000)}}
        )
        return response.parsed if isinstance(response.parsed, EmailContent) else None

    def _violations(self, content: EmailContent, facts: SummaryFacts, state: SessionState) -> bool:
        text = "\n".join(
            [
                content.subject,
                content.greeting,
                *content.discussed,
                *content.outcomes,
                *content.next_steps,
                content.closing,
            ]
        )
        ctx = GuardContext(
            verified=True,
            party_id=state.memory.identity.party_id,
            phase=Phase.POST_PROCESS,
            caller_texts=[],
            caller_identity=state.memory.identity.valid_values(),
            grounding_text=facts.as_json(),
        )
        violations = self._guard.check(text, ctx)
        if violations:
            _log.warning(
                "summary draft blocked",
                extra={"fields": {"rules": sorted({v.rule.value for v in violations})}},
            )
        return bool(violations)
