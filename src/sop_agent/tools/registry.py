"""ToolRegistry: phase whitelist and ownership checks for every tool call (SPEC §11.2, INV-4)."""

from typing import Any

from pydantic import BaseModel

from sop_agent.data.repository import InMemoryRepository
from sop_agent.domain.enums import Phase
from sop_agent.llm.base import ToolSpec
from sop_agent.sop.phases import PHASES, ToolName
from sop_agent.tools.read_tools import FUNCTIONS, SPECS, ReadTools, ToolContext, ToolInputError

NOT_IN_PHASE = "tool not available in this phase"


class ToolResult(BaseModel):
    ok: bool
    data: dict[str, Any] | None = None
    error: str | None = None
    denied: bool = False  # Not whitelisted for the phase (TOOL_DENIED)
    case_ids: list[str] = []


class ToolRegistry:
    def __init__(self, repo: InMemoryRepository) -> None:
        self._tools = ReadTools(repo)

    def specs_for(self, phase: Phase) -> list[ToolSpec]:
        """Only the tools whitelisted for the phase, in a stable order (prompt caching)."""
        allowed = PHASES[phase].allowed_tools
        return [SPECS[name] for name in ToolName if name in allowed]

    async def execute(self, name: str, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        """Whitelist, then ownership. `party_id` in `args`, if the model sends one, is ignored (INV-4)."""
        tool = next((t for t in ToolName if t.value == name), None)
        if tool is None or tool not in PHASES[ctx.phase].allowed_tools:
            return ToolResult(ok=False, error=NOT_IN_PHASE, denied=True)
        clean = {k: v for k, v in args.items() if k != "party_id"}
        try:
            data = await FUNCTIONS[tool](self._tools, ctx, clean)
        except ToolInputError as exc:
            return ToolResult(ok=False, error=str(exc))
        return ToolResult(ok=True, data=data, case_ids=_case_ids(data))


def _case_ids(data: dict[str, Any]) -> list[str]:
    """Claims a result was about (for case_log.discussed_case_ids). A plain listing discusses none (D48)."""
    return [str(data["case_id"])] if "case_id" in data else []
