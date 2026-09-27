"""Responder: turns the directive and grounding into one reply, with the read-only tool loop.

Prompt layers: global system prompt, phase instructions, <directive> (YAML), <grounding> (JSON). Caller messages
are always user messages, never spliced into the system prompt. Tools are offered only where the phase allows.
"""

import json
import time
from dataclasses import dataclass, field
from typing import Any, Literal, get_args

import yaml

from sop_agent.agent.context import Grounding
from sop_agent.agent.prompt_loader import render
from sop_agent.llm.base import LLMClient, LLMError, LLMMessage, LLMRequest, LLMResponse, Role, ToolSpec
from sop_agent.memory.state import ChatRole, SessionState
from sop_agent.observability.trace import LLMCallRecord
from sop_agent.sop.directive import TurnDirective
from sop_agent.tools.read_tools import ToolContext
from sop_agent.tools.registry import ToolRegistry, ToolResult

HISTORY_TURNS = 10
RESPONDER_MAX_TOKENS = 2000
COMPONENT = "responder"
DIRECTIVE_EXCLUDE = {"fallback_reply", "grounding"}
Effort = Literal["low", "medium", "high"]
GUARD_REQUIREMENTS: dict[str, str] = {
    "G1": "Before verification, mention no account record details the caller didn't say themselves.",
    "G2": "Mention nothing that belongs to another policyholder.",
    "G3": "Don't repeat the caller's date of birth, ID digits or full phone number.",
    "G4": "Use only amounts and dates that appear in GROUNDING or tool results.",
    "G5": "Don't say an action happened unless it is in events.",
}


@dataclass(frozen=True)
class ResponderPrompts:
    system: str
    phases: dict[str, str]


@dataclass
class ToolTrace:
    name: str
    ok: bool
    denied: bool
    case_ids: list[str]
    error: str | None = None


@dataclass
class ResponderResult:
    text: str
    tool_calls: list[ToolTrace] = field(default_factory=list)
    tool_results_text: str = ""
    llm_calls: list[LLMCallRecord] = field(default_factory=list)


class Responder:
    def __init__(
        self,
        client: LLMClient,
        model: str,
        prompts: ResponderPrompts,
        registry: ToolRegistry,
        *,
        agent_name: str,
        company_name: str,
        effort: str | None = None,
        max_rounds: int = 3,
        max_tokens: int = RESPONDER_MAX_TOKENS,
    ) -> None:
        self._client = client
        self._model = model
        self._prompts = prompts
        self._registry = registry
        self._names = {"agent_name": agent_name, "company_name": company_name}
        self._effort = _effort(effort)
        self._max_rounds = max_rounds
        self._max_tokens = max_tokens

    async def respond(
        self,
        state: SessionState,
        directive: TurnDirective,
        grounding: Grounding,
        guard_notes: list[str] | None = None,
    ) -> ResponderResult:
        """Run the tool loop (at most `max_rounds`), then one final request without tools if needed."""
        system = self.system_prompt(state, directive, grounding, guard_notes or [])
        base = history_messages(state)
        messages = list(base)
        tools = self._registry.specs_for(state.phase)
        result = ResponderResult(text="")
        tool_outputs: list[dict[str, Any]] = []
        for _ in range(self._max_rounds if tools else 0):
            response = await self._call(result, self._request(system, messages, tools))
            if not response.tool_calls:
                result.text = response.text.strip()
                result.tool_results_text = json.dumps(tool_outputs, default=str)
                return result
            messages.append(LLMMessage(role=Role.ASSISTANT, content=response.raw_assistant_content))
            messages.append(
                LLMMessage(
                    role=Role.USER, content=await self._run_tools(state, response, result, tool_outputs)
                )
            )
        final_system = system
        if tool_outputs:
            final_system += (
                "\n\n<tool_results>\n" + json.dumps(tool_outputs, indent=1, default=str) + "\n</tool_results>"
            )
        response = await self._call(result, self._request(final_system, base, []))
        result.text = response.text.strip()
        result.tool_results_text = json.dumps(tool_outputs, default=str)
        return result

    def system_prompt(
        self, state: SessionState, directive: TurnDirective, grounding: Grounding, guard_notes: list[str]
    ) -> str:
        directive_yaml = yaml.safe_dump(
            directive.model_dump(mode="json", exclude=DIRECTIVE_EXCLUDE), sort_keys=False, allow_unicode=True
        )
        parts = [
            render(self._prompts.system, **self._names),
            self._prompts.phases[state.phase.value],
            f"<directive>\n{directive_yaml}</directive>",
            f"<grounding>\n{grounding.as_json()}\n</grounding>",
        ]
        if guard_notes:
            notes = "\n".join(f"- {rule}: {GUARD_REQUIREMENTS.get(rule, '')}" for rule in guard_notes)
            parts.append(
                f"<guard_note>\nYour previous draft broke these rules. Rewrite it.\n{notes}\n</guard_note>"
            )
        return "\n\n".join(parts)

    def _request(self, system: str, messages: list[LLMMessage], tools: list[ToolSpec]) -> LLMRequest:
        return LLMRequest(
            model=self._model,
            system=system,
            messages=messages,
            tools=tools,
            max_tokens=self._max_tokens,
            effort=self._effort,
        )

    async def _call(self, result: ResponderResult, request: LLMRequest) -> LLMResponse:
        started = time.perf_counter()
        try:
            response = await self._client.create(request)
        except LLMError:
            result.llm_calls.append(_record(request.model, started, None))
            raise
        result.llm_calls.append(_record(request.model, started, response))
        return response

    async def _run_tools(
        self,
        state: SessionState,
        response: LLMResponse,
        result: ResponderResult,
        outputs: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        ctx = ToolContext(
            party_id=state.memory.identity.party_id or "",
            phase=state.phase,
            today=state.session_settings.today,
        )
        blocks: list[dict[str, Any]] = []
        for call in response.tool_calls:
            outcome: ToolResult = await self._registry.execute(call.name, call.input, ctx)
            result.tool_calls.append(
                ToolTrace(call.name, outcome.ok, outcome.denied, outcome.case_ids, outcome.error)
            )
            payload = outcome.data if outcome.ok else {"error": outcome.error}
            outputs.append({"tool": call.name, "result": payload})
            blocks.append(
                {
                    "type": "tool_result",
                    "tool_use_id": call.id,
                    "content": json.dumps(payload, default=str),
                    "is_error": not outcome.ok,
                }
            )
        return blocks  # All results in one user message, so parallel calls stay natural


def history_messages(state: SessionState) -> list[LLMMessage]:
    """The last turns as user/assistant messages, starting and ending with the caller."""
    turns = state.history[-HISTORY_TURNS:]
    while turns and turns[0].role is not ChatRole.CALLER:
        turns = turns[1:]
    return [
        LLMMessage(role=Role.USER if t.role is ChatRole.CALLER else Role.ASSISTANT, content=t.text)
        for t in turns
    ]


def _record(model: str, started: float, response: LLMResponse | None) -> LLMCallRecord:
    return LLMCallRecord(
        component=COMPONENT,
        model=model,
        latency_ms=int((time.perf_counter() - started) * 1000),
        input_tokens=response.usage.input_tokens if response else 0,
        output_tokens=response.usage.output_tokens if response else 0,
        ok=response is not None,
    )


def _effort(value: str | None) -> Effort | None:
    """RESPONDER_EFFORT, sent only when configured to a supported level."""
    for level in get_args(Effort):
        if value == level:
            return level  # type: ignore[no-any-return]
    return None
