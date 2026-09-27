"""FakeLLMClient: scripted responses plus request recording, for tests and offline runs (SPEC §10.1).

A script item can be an LLMResponse, a parsed BaseModel (wrapped into a response), or an exception to raise.
A callable script receives each request and returns one of those.
"""

from collections.abc import Callable, Iterable

from pydantic import BaseModel

from sop_agent.llm.base import LLMRequest, LLMResponse, ensure_no_prefill

ScriptItem = LLMResponse | BaseModel | Exception
Script = Iterable[ScriptItem] | Callable[[LLMRequest], ScriptItem]


class ScriptExhaustedError(AssertionError):
    """The code under test made more LLM calls than the test scripted."""


class FakeLLMClient:
    def __init__(self, script: Script = ()) -> None:
        self._fn = script if callable(script) else None
        self._items = [] if callable(script) else list(script)
        self.requests: list[LLMRequest] = []

    def queue(self, *items: ScriptItem) -> None:
        """Append scripted items (for tests that build the script after constructing the code under test)."""
        self._items.extend(items)

    async def create(self, request: LLMRequest) -> LLMResponse:
        ensure_no_prefill(request)
        self.requests.append(request)
        item = self._next(request)
        if isinstance(item, Exception):
            raise item
        if isinstance(item, LLMResponse):
            return item
        return LLMResponse(text=item.model_dump_json(), parsed=item)

    def _next(self, request: LLMRequest) -> ScriptItem:
        if self._fn is not None:
            return self._fn(request)
        if not self._items:
            raise ScriptExhaustedError("FakeLLMClient has no scripted response left")
        return self._items.pop(0)
