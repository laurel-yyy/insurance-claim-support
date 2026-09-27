"""The Anthropic adapter follows every LLM API rule. The SDK is replaced by a stub that records the call."""

import json
from typing import Any, cast

import anthropic
import httpx2
import pytest
from anthropic.types import Message
from pydantic import BaseModel

from sop_agent.llm.anthropic_client import AnthropicClient, build_params
from sop_agent.llm.base import (
    LLMMessage,
    LLMOutputError,
    LLMRequest,
    LLMTimeoutError,
    LLMUnavailableError,
    Role,
    ToolSpec,
)

SECRET = "sk-ant-test-never-shown"
FORBIDDEN_PARAMS = {"temperature", "top_p", "top_k", "tool_choice", "stop_sequences"}


class Answer(BaseModel):
    label: str


def _message(content: list[dict[str, Any]], stop_reason: str = "end_turn") -> Message:
    return Message.model_validate(
        {
            "id": "msg_1",
            "type": "message",
            "role": "assistant",
            "model": "claude-test",
            "content": content,
            "stop_reason": stop_reason,
            "stop_sequence": None,
            "usage": {"input_tokens": 11, "output_tokens": 7},
        }
    )


class _StubMessages:
    def __init__(self, result: Message | Exception) -> None:
        self.result = result
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Message:
        self.calls.append(kwargs)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class _StubSDK:
    def __init__(self, result: Message | Exception) -> None:
        self.messages = _StubMessages(result)


def _client(result: Message | Exception) -> tuple[AnthropicClient, _StubMessages]:
    sdk = _StubSDK(result)
    return AnthropicClient(sdk=cast(anthropic.AsyncAnthropic, sdk)), sdk.messages


def _request(**overrides: Any) -> LLMRequest:
    base: dict[str, Any] = {
        "model": "claude-test",
        "system": "system prompt",
        "messages": [LLMMessage(role=Role.USER, content="hi")],
    }
    return LLMRequest(**{**base, **overrides})


def _schema_has(schema: Any, key: str) -> bool:
    if isinstance(schema, dict):
        return key in schema or any(_schema_has(v, key) for v in schema.values())
    if isinstance(schema, list):
        return any(_schema_has(v, key) for v in schema)
    return False


def test_request_never_carries_sampling_params_or_tool_choice() -> None:
    tool = ToolSpec(
        name="list_claims",
        description="List claims",
        input_schema={"type": "object", "properties": {}, "required": []},
    )
    params = build_params(_request(tools=[tool], output_model=Answer, effort="low"))
    assert not FORBIDDEN_PARAMS & set(params)
    assert set(params) <= {"model", "max_tokens", "system", "messages", "output_config", "tools", "thinking"}


def test_structured_output_uses_output_config_format_with_strict_schema() -> None:
    params = build_params(_request(output_model=Answer))
    fmt = params["output_config"]["format"]
    assert fmt["type"] == "json_schema"
    assert fmt["schema"]["additionalProperties"] is False
    assert fmt["schema"]["required"] == ["label"]
    assert "effort" not in params["output_config"]


def test_effort_is_sent_only_when_configured() -> None:
    assert "output_config" not in build_params(_request())
    assert build_params(_request(effort="low"))["output_config"] == {"effort": "low"}


def test_thinking_is_left_at_the_model_default_unless_disabled() -> None:
    assert "thinking" not in build_params(_request())
    assert build_params(_request(disable_thinking=True))["thinking"] == {"type": "disabled"}


def test_tools_are_strict_with_closed_schemas() -> None:
    tool = ToolSpec(
        name="get_claim",
        description="Get one claim",
        input_schema={
            "type": "object",
            "properties": {"case_id": {"type": "string"}},
            "required": ["case_id"],
        },
    )
    [param] = build_params(_request(tools=[tool]))["tools"]
    assert param["strict"] is True
    assert param["input_schema"]["additionalProperties"] is False


async def test_assistant_prefill_is_rejected_before_sending() -> None:
    client, stub = _client(_message([{"type": "text", "text": "x"}]))
    prefilled = _request(
        messages=[LLMMessage(role=Role.USER, content="hi"), LLMMessage(role=Role.ASSISTANT, content="{")]
    )
    with pytest.raises(ValueError, match="prefill"):
        await client.create(prefilled)
    assert stub.calls == []


async def test_content_is_read_by_block_type_and_raw_blocks_are_kept() -> None:
    content: list[dict[str, Any]] = [
        {"type": "thinking", "thinking": "", "signature": "sig"},
        {"type": "text", "text": "Checking."},
        {"type": "tool_use", "id": "toolu_1", "name": "get_claim", "input": {"case_id": "CL-1"}},
    ]
    client, _ = _client(_message(content, stop_reason="tool_use"))
    response = await client.create(_request())
    assert response.text == "Checking."
    assert [(c.id, c.name, c.input) for c in response.tool_calls] == [
        ("toolu_1", "get_claim", {"case_id": "CL-1"})
    ]
    assert [b["type"] for b in response.raw_assistant_content] == ["thinking", "text", "tool_use"]
    assert response.raw_assistant_content[0]["signature"] == "sig"
    assert (response.usage.input_tokens, response.usage.output_tokens) == (11, 7)


async def test_structured_output_is_validated() -> None:
    client, _ = _client(
        _message(
            [
                {"type": "thinking", "thinking": "", "signature": "s"},
                {"type": "text", "text": '{"label": "ok"}'},
            ]
        )
    )
    response = await client.create(_request(output_model=Answer))
    assert isinstance(response.parsed, Answer) and response.parsed.label == "ok"


@pytest.mark.parametrize("stop_reason", ["refusal", "max_tokens"])
async def test_refusal_or_truncation_with_schema_is_an_output_error(stop_reason: str) -> None:
    client, _ = _client(_message([{"type": "text", "text": '{"label": "ok"}'}], stop_reason=stop_reason))
    with pytest.raises(LLMOutputError) as info:
        await client.create(_request(output_model=Answer))
    assert info.value.stop_reason == stop_reason


async def test_refusal_without_schema_is_an_output_error() -> None:
    client, _ = _client(_message([{"type": "text", "text": ""}], stop_reason="refusal"))
    with pytest.raises(LLMOutputError):
        await client.create(_request())


@pytest.mark.parametrize("text", ["not json", '{"other": 1}', ""])
async def test_invalid_structured_output_is_an_output_error(text: str) -> None:
    client, _ = _client(_message([{"type": "text", "text": text}]))
    with pytest.raises(LLMOutputError):
        await client.create(_request(output_model=Answer))


_REQUEST = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (anthropic.APITimeoutError(request=_REQUEST), LLMTimeoutError),
        (anthropic.APIConnectionError(request=_REQUEST), LLMUnavailableError),
        (
            anthropic.InternalServerError(
                f"boom {SECRET}", response=httpx2.Response(500, request=_REQUEST), body=None
            ),
            LLMUnavailableError,
        ),
    ],
)
async def test_sdk_errors_are_mapped_and_never_expose_the_key(
    error: Exception, expected: type[Exception]
) -> None:
    client, _ = _client(error)
    with pytest.raises(expected) as info:
        await client.create(_request())
    assert SECRET not in str(info.value)


def test_client_repr_never_contains_the_key() -> None:
    client = AnthropicClient(api_key=SECRET)
    assert SECRET not in repr(client)


def test_messages_are_passed_in_order_with_roles() -> None:
    request = _request(
        messages=[
            LLMMessage(role=Role.USER, content="a"),
            LLMMessage(role=Role.ASSISTANT, content="b"),
            LLMMessage(role=Role.USER, content="c"),
        ]
    )
    params = build_params(request)
    assert [(m["role"], m["content"]) for m in params["messages"]] == [
        ("user", "a"),
        ("assistant", "b"),
        ("user", "c"),
    ]
    assert json.dumps(params["system"]) == '"system prompt"'
