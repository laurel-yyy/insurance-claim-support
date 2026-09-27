"""TurnTrace: one masked JSONL line per turn in var/traces/{session_id}.jsonl (SPEC §16)."""

import json
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, Field

from sop_agent.observability.masking import mask_data


class LLMCallRecord(BaseModel):
    component: str
    model: str
    latency_ms: int
    input_tokens: int = 0
    output_tokens: int = 0
    ok: bool = True


class TurnTrace(BaseModel):
    session_id: str
    turn: int
    phase_before: str
    phase_after: str
    verify_stage: str | None = None
    nlu: dict[str, Any] = Field(default_factory=dict)
    observations: dict[str, Any] = Field(default_factory=dict)
    events: list[dict[str, Any]] = Field(default_factory=list)
    directive: dict[str, Any] = Field(default_factory=dict)
    actions: list[dict[str, Any]] = Field(default_factory=list)
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    guard: list[dict[str, Any]] = Field(default_factory=list)
    fallback_used: bool = False
    llm_calls: list[LLMCallRecord] = Field(default_factory=list)
    latency_ms: int = 0

    def masked(self) -> dict[str, Any]:
        data: dict[str, Any] = mask_data(self.model_dump(mode="json"))
        return data


class TraceSink(Protocol):
    def record(self, trace: TurnTrace) -> None: ...


class JsonlTraceWriter:
    """Appends masked traces; the directory is created on first write."""

    def __init__(self, directory: Path) -> None:
        self._directory = directory

    def record(self, trace: TurnTrace) -> None:
        self._directory.mkdir(parents=True, exist_ok=True)
        path = self._directory / f"{trace.session_id}.jsonl"
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(trace.masked(), ensure_ascii=False) + "\n")


class MemoryTraceSink:
    """Keeps masked traces in memory (tests, debug view)."""

    def __init__(self) -> None:
        self.traces: list[dict[str, Any]] = []

    def record(self, trace: TurnTrace) -> None:
        self.traces.append(trace.masked())
