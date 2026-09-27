"""Provider-agnostic LLM interface (SPEC §10.1). Knows nothing about insurance or the SOP.

Requests have no fields for sampling parameters or tool_choice, so no caller can send them (§10.2).
"""

from enum import StrEnum
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field


class Role(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"


class LLMMessage(BaseModel):
    """A conversation message. `content` is text, or raw content blocks (tool loops)."""

    role: Role
    content: str | list[dict[str, Any]]


class ToolSpec(BaseModel):
    """A read-only tool; every input parameter must be required (strict tools, §10.2)."""

    name: str
    description: str
    input_schema: dict[str, Any]


class LLMRequest(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    model: str
    system: str
    messages: list[LLMMessage]
    tools: list[ToolSpec] = Field(default_factory=list)
    output_model: type[BaseModel] | None = None  # Structured output, validated with this model
    max_tokens: int = 1024
    effort: Literal["low", "medium", "high"] | None = None
    disable_thinking: bool = False


class ToolCall(BaseModel):
    id: str
    name: str
    input: dict[str, Any]


class TokenUsage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0


class LLMResponse(BaseModel):
    """Content read by block type; thinking blocks are excluded from `text`."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    text: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    parsed: BaseModel | None = None
    stop_reason: str = "end_turn"
    # Every content block as returned, sent back verbatim in tool loops (thinking blocks included)
    raw_assistant_content: list[dict[str, Any]] = Field(default_factory=list)
    usage: TokenUsage = Field(default_factory=TokenUsage)
    model: str = ""


class LLMError(Exception):
    """Base for every LLM failure. Messages never contain API keys or request content."""


class LLMTimeoutError(LLMError):
    pass


class LLMUnavailableError(LLMError):
    """The provider couldn't be reached or returned an API error."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class LLMOutputError(LLMError):
    """The model answered, but not usably: refusal, truncation, or output that fails the schema."""

    def __init__(self, message: str, stop_reason: str | None = None) -> None:
        super().__init__(message)
        self.stop_reason = stop_reason


class LLMClient(Protocol):
    async def create(self, request: LLMRequest) -> LLMResponse: ...


def ensure_no_prefill(request: LLMRequest) -> None:
    """Assistant prefill returns a 400 on current models (§10.2); the last message must be the user's."""
    if not request.messages or request.messages[-1].role is not Role.USER:
        raise ValueError("the last message must be a user message (assistant prefill is not allowed)")
