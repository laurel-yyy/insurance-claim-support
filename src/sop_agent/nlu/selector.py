"""ClaimSelector: a bounded LLM choice among candidate claims (SPEC §8.2.6).

Called only by the orchestrator while a CHOOSE_CLAIM question is open, so the caller is verified. Candidate IDs go
in the message content, never in the schema. Output is checked here and again by the policy.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from sop_agent.domain.enums import Path
from sop_agent.llm.base import LLMClient, LLMError, LLMMessage, LLMRequest, Role
from sop_agent.nlu.wire import NONE, SelectorWire
from sop_agent.observability.logging import get_logger
from sop_agent.sop.directive import SelectorChoice

SELECTOR_MAX_TOKENS = 400

_log = get_logger(__name__)


@dataclass(frozen=True)
class SelectorCandidate:
    case_id: str
    summary: str  # One line: type, creation date, status, summary


class ClaimSelector:
    def __init__(
        self, client: LLMClient, model: str, prompt_template: str, max_tokens: int = SELECTOR_MAX_TOKENS
    ) -> None:
        self._client = client
        self._model = model
        self._template = prompt_template
        self._max_tokens = max_tokens

    async def select(
        self, message: str, candidates: Sequence[SelectorCandidate], allowed_paths: Sequence[Path]
    ) -> SelectorChoice:
        """Never raises; an unclear or invalid answer is an empty choice."""
        if not candidates:
            return SelectorChoice()
        try:
            response = await self._client.create(self.build_request(message, candidates, allowed_paths))
        except LLMError as exc:
            _log.warning("selector call failed", extra={"fields": {"error": type(exc).__name__}})
            return SelectorChoice()
        if not isinstance(response.parsed, SelectorWire):
            return SelectorChoice()
        return validate_choice(response.parsed, candidates, allowed_paths)

    def build_request(
        self, message: str, candidates: Sequence[SelectorCandidate], allowed_paths: Sequence[Path]
    ) -> LLMRequest:
        listed = "\n".join(f"{i}. {c.case_id}: {c.summary}" for i, c in enumerate(candidates, start=1))
        paths = ", ".join(p.value for p in allowed_paths) or NONE
        content = f"CANDIDATES:\n{listed}\n\nALLOWED_PATHS: {paths}\n\nCALLER_MESSAGE:\n{message}"
        return LLMRequest(
            model=self._model,
            system=self._template,
            messages=[LLMMessage(role=Role.USER, content=content)],
            output_model=SelectorWire,
            max_tokens=self._max_tokens,
        )


def validate_choice(
    wire: SelectorWire, candidates: Sequence[SelectorCandidate], allowed_paths: Sequence[Path]
) -> SelectorChoice:
    ids = {c.case_id.upper() for c in candidates}
    case_id = wire.case_id.strip().upper()
    path = Path(wire.path.value) if wire.path.value != NONE else None
    return SelectorChoice(
        case_id=case_id if case_id in ids else "",
        path=path if path in allowed_paths else None,
    )
