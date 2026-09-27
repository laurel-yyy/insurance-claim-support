"""Responder: prompt layering and the read-only tool loop."""

from typing import Any

import pytest

from sop_agent.agent.context import ContextBuilder, Grounding
from sop_agent.agent.responder import Responder, ResponderPrompts
from sop_agent.container import load_prompts
from sop_agent.data.repository import InMemoryRepository
from sop_agent.domain.enums import Path, Phase
from sop_agent.llm.base import LLMRequest, LLMResponse, Role, ToolCall
from sop_agent.llm.fake import FakeLLMClient
from sop_agent.memory.state import ChatRole, ChatTurn, SessionState
from sop_agent.sop.directive import Observations, TurnDirective
from sop_agent.tools.registry import ToolRegistry
from tests.builders import new_state, nlu
from tests.policy_helpers import engine, verified


def _responder(repo: InMemoryRepository, fake: FakeLLMClient, **kwargs: Any) -> Responder:
    prompts: ResponderPrompts = load_prompts().responder
    return Responder(
        fake,
        "claude-sonnet-test",
        prompts,
        ToolRegistry(repo),
        agent_name="Morgan",
        company_name="Northwind Insurance",
        **kwargs,
    )


def _case_state() -> SessionState:
    state = verified(new_state(), "P9", Phase.PROCESS_CASE)
    state.memory.selected_case_id, state.memory.selected_path = "CL-2048", Path.DENIAL_QUESTION
    state.history.append(ChatTurn(role=ChatRole.AGENT, text="Hi", turn=0))
    state.history.append(ChatTurn(role=ChatRole.CALLER, text="What's the status?", turn=1))
    return state


def _inputs(repo: InMemoryRepository, state: SessionState) -> tuple[TurnDirective, Grounding]:
    directive = (
        engine(repo).decide(state, nlu(), Observations()).directive.model_copy(update={"phase": state.phase})
    )
    return directive, ContextBuilder(repo, "faq").build(state, directive)


def _tool_use(call_id: str, name: str, args: dict[str, Any]) -> LLMResponse:
    return LLMResponse(
        tool_calls=[ToolCall(id=call_id, name=name, input=args)],
        stop_reason="tool_use",
        raw_assistant_content=[
            {"type": "thinking", "thinking": "", "signature": "sig"},
            {"type": "tool_use", "id": call_id, "name": name, "input": args},
        ],
    )


async def test_prompt_layers_and_messages(snapshot_repo: InMemoryRepository) -> None:
    fake = FakeLLMClient([LLMResponse(text="Your claim CL-2048 is denied.")])
    state = _case_state()
    directive, grounding = _inputs(snapshot_repo, state)
    result = await _responder(snapshot_repo, fake, effort="low").respond(state, directive, grounding)
    assert result.text == "Your claim CL-2048 is denied."
    [request] = fake.requests
    assert request.system.startswith("You are Morgan, a claims support agent for Northwind Insurance")
    assert "PHASE: PROCESS_CASE (guided)" in request.system
    assert "<directive>" in request.system and "resume_anchor:" in request.system
    assert "fallback_reply" not in request.system
    assert "<grounding>" in request.system and '"selected_claim"' in request.system
    assert [m.role for m in request.messages] == [Role.USER]  # history starts with the caller
    assert {t.name for t in request.tools} == {
        "list_claims",
        "get_claim",
        "get_document_guidance",
        "get_followup_guidance",
    }
    assert request.effort == "low"


async def test_no_tools_before_verification(snapshot_repo: InMemoryRepository) -> None:
    fake = FakeLLMClient([LLMResponse(text="Could you share your date of birth?")])
    state = new_state()
    state.history.append(ChatTurn(role=ChatRole.CALLER, text="hi", turn=1))
    directive, grounding = _inputs(snapshot_repo, state)
    await _responder(snapshot_repo, fake).respond(state, directive, grounding)
    assert fake.requests[0].tools == []


async def test_tool_loop_echoes_raw_blocks_and_returns_results(snapshot_repo: InMemoryRepository) -> None:
    fake = FakeLLMClient(
        [_tool_use("t1", "get_claim", {"case_id": "CL-2048"}), LLMResponse(text="It's denied.")]
    )
    state = _case_state()
    directive, grounding = _inputs(snapshot_repo, state)
    result = await _responder(snapshot_repo, fake).respond(state, directive, grounding)
    assert result.text == "It's denied."
    followup: LLMRequest = fake.requests[1]
    assistant, tool_results = followup.messages[-2], followup.messages[-1]
    assert isinstance(assistant.content, list) and isinstance(tool_results.content, list)
    assert assistant.role is Role.ASSISTANT and assistant.content[0]["type"] == "thinking"
    assert tool_results.role is Role.USER and tool_results.content[0]["tool_use_id"] == "t1"
    assert [c.name for c in result.tool_calls] == ["get_claim"] and result.tool_calls[0].case_ids == [
        "CL-2048"
    ]
    assert "$1,450.00" in result.tool_results_text


async def test_foreign_claim_via_tool_gets_the_generic_error(snapshot_repo: InMemoryRepository) -> None:
    fake = FakeLLMClient(
        [_tool_use("t1", "get_claim", {"case_id": "CL-3001"}), LLMResponse(text="I can't find that one.")]
    )
    state = _case_state()
    directive, grounding = _inputs(snapshot_repo, state)
    result = await _responder(snapshot_repo, fake).respond(state, directive, grounding)
    content = fake.requests[1].messages[-1].content
    assert isinstance(content, list)
    [block] = content
    assert block["is_error"] and "No claim with that ID" in block["content"]
    assert result.tool_calls[0].ok is False


@pytest.mark.parametrize("rounds", [1, 3])
async def test_round_limit_then_one_request_without_tools(
    snapshot_repo: InMemoryRepository, rounds: int
) -> None:
    script = [_tool_use(f"t{i}", "list_claims", {}) for i in range(rounds)] + [
        LLMResponse(text="Here's what I found.")
    ]
    fake = FakeLLMClient(script)
    state = _case_state()
    directive, grounding = _inputs(snapshot_repo, state)
    result = await _responder(snapshot_repo, fake, max_rounds=rounds).respond(state, directive, grounding)
    assert result.text == "Here's what I found."
    assert len(fake.requests) == rounds + 1
    final = fake.requests[-1]
    assert final.tools == [] and "<tool_results>" in final.system
    assert all(isinstance(m.content, str) for m in final.messages)  # no dangling tool blocks without tools


async def test_guard_notes_name_rules_only(snapshot_repo: InMemoryRepository) -> None:
    fake = FakeLLMClient([LLMResponse(text="ok")])
    state = _case_state()
    directive, grounding = _inputs(snapshot_repo, state)
    await _responder(snapshot_repo, fake).respond(state, directive, grounding, guard_notes=["G2", "G4"])
    system = fake.requests[0].system
    assert "<guard_note>" in system and "- G2:" in system and "- G4:" in system
