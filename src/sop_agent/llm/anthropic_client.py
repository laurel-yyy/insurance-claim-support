"""Anthropic adapter (SPEC §10.2).

Rules this module enforces: no temperature/top_p/top_k, no assistant prefill, no forced tool_choice (the API
default `auto` is used), structured output via `output_config.format` validated with Pydantic, `effort` only
when configured, content read by block type, and raw assistant blocks kept for tool loops.
"""

from typing import Any, cast

import anthropic
from anthropic.types import Message, MessageParam, OutputConfigParam, ThinkingConfigParam, ToolParam
from pydantic import BaseModel, ValidationError

from sop_agent.llm.base import (
    LLMOutputError,
    LLMRequest,
    LLMResponse,
    LLMTimeoutError,
    LLMUnavailableError,
    TokenUsage,
    ToolCall,
    ToolSpec,
    ensure_no_prefill,
)

UNUSABLE_WITH_SCHEMA = frozenset({"refusal", "max_tokens"})


def strict_schema(model: type[BaseModel] | dict[str, Any]) -> dict[str, Any]:
    """The JSON schema the API expects: the SDK's own transform (additionalProperties, constraints)."""
    return anthropic.transform_schema(model)


class AnthropicClient:
    """LLMClient backed by AsyncAnthropic. The API key lives only inside the SDK client."""

    def __init__(
        self,
        api_key: str | None = None,
        timeout_seconds: float = 30.0,
        *,
        sdk: anthropic.AsyncAnthropic | None = None,
    ) -> None:
        self._sdk = sdk or anthropic.AsyncAnthropic(api_key=api_key, timeout=timeout_seconds, max_retries=2)

    def __repr__(self) -> str:
        return "AnthropicClient()"  # Never expose the key

    async def create(self, request: LLMRequest) -> LLMResponse:
        ensure_no_prefill(request)
        try:
            message = await self._sdk.messages.create(**build_params(request))
        except anthropic.APITimeoutError as exc:
            raise LLMTimeoutError("LLM request timed out") from exc
        except anthropic.APIStatusError as exc:
            raise LLMUnavailableError(
                f"LLM API error {exc.status_code}", status_code=exc.status_code
            ) from exc
        except anthropic.APIConnectionError as exc:
            raise LLMUnavailableError("LLM connection failed") from exc
        return parse_message(message, request.output_model)


def build_params(request: LLMRequest) -> dict[str, Any]:
    """Only documented, allowed parameters. Nothing here can carry sampling settings or tool_choice."""
    params: dict[str, Any] = {
        "model": request.model,
        "max_tokens": request.max_tokens,
        "system": request.system,
        "messages": [
            cast(MessageParam, {"role": m.role.value, "content": m.content}) for m in request.messages
        ],
    }
    output_config: OutputConfigParam = {}
    if request.output_model is not None:
        output_config["format"] = {"type": "json_schema", "schema": strict_schema(request.output_model)}
    if request.effort is not None:
        output_config["effort"] = request.effort
    if output_config:
        params["output_config"] = output_config
    if request.tools:
        params["tools"] = [_tool_param(t) for t in request.tools]
    if request.disable_thinking:
        thinking: ThinkingConfigParam = {"type": "disabled"}
        params["thinking"] = thinking
    return params


def _tool_param(tool: ToolSpec) -> ToolParam:
    return cast(
        ToolParam,
        {
            "name": tool.name,
            "description": tool.description,
            "input_schema": strict_schema(tool.input_schema),
            "strict": True,
        },
    )


def parse_message(message: Message, output_model: type[BaseModel] | None) -> LLMResponse:
    """Read blocks by type (thinking may come first); validate structured output when requested."""
    stop_reason = message.stop_reason or ""
    texts: list[str] = []
    calls: list[ToolCall] = []
    for block in message.content:
        if block.type == "text":
            texts.append(block.text)
        elif block.type == "tool_use":
            calls.append(
                ToolCall(id=block.id, name=block.name, input=dict(cast(dict[str, Any], block.input)))
            )
    text = "".join(texts)
    parsed: BaseModel | None = None
    if output_model is not None:
        if stop_reason in UNUSABLE_WITH_SCHEMA:
            raise LLMOutputError(f"structured output unusable (stop_reason={stop_reason})", stop_reason)
        try:
            parsed = output_model.model_validate_json(text)
        except ValidationError as exc:
            raise LLMOutputError("structured output failed validation", stop_reason) from exc
    elif stop_reason == "refusal":
        raise LLMOutputError("the model declined the request", stop_reason)
    usage = message.usage
    return LLMResponse(
        text=text,
        tool_calls=calls,
        parsed=parsed,
        stop_reason=stop_reason,
        raw_assistant_content=[block.to_dict() for block in message.content],
        usage=TokenUsage(
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_read_input_tokens=usage.cache_read_input_tokens or 0,
            cache_creation_input_tokens=usage.cache_creation_input_tokens or 0,
        ),
        model=message.model,
    )
