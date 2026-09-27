"""ToolRegistry: phase whitelist, ownership and the no-party_id rule (SPEC §11.1-§11.2, INV-4)."""

import json
from datetime import date

import pytest

from sop_agent.data.repository import InMemoryRepository
from sop_agent.domain.enums import Phase
from sop_agent.sop.phases import ToolName
from sop_agent.tools.read_tools import NOT_AVAILABLE, ToolContext
from sop_agent.tools.registry import NOT_IN_PHASE, ToolRegistry

TODAY = date(2026, 3, 10)


@pytest.fixture
def registry(snapshot_repo: InMemoryRepository) -> ToolRegistry:
    return ToolRegistry(snapshot_repo)


def _ctx(phase: Phase = Phase.PROCESS_CASE, party: str = "P9") -> ToolContext:
    return ToolContext(party_id=party, phase=phase, today=TODAY)


def test_specs_follow_the_phase_whitelist(registry: ToolRegistry) -> None:
    assert registry.specs_for(Phase.VERIFY_ID) == []
    assert [s.name for s in registry.specs_for(Phase.RESOLVE_INTENT)] == ["list_claims"]
    assert {s.name for s in registry.specs_for(Phase.PROCESS_CASE)} == {t.value for t in ToolName}
    assert registry.specs_for(Phase.POST_PROCESS) == []


def test_party_id_is_never_a_tool_parameter(registry: ToolRegistry) -> None:
    for spec in registry.specs_for(Phase.PROCESS_CASE):
        assert "party_id" not in json.dumps(spec.input_schema)
        assert set(spec.input_schema["required"]) == set(spec.input_schema["properties"])


async def test_tool_outside_phase_is_denied(registry: ToolRegistry) -> None:
    result = await registry.execute("get_claim", {"case_id": "CL-2048"}, _ctx(Phase.RESOLVE_INTENT))
    assert (result.ok, result.denied, result.error) == (False, True, NOT_IN_PHASE)
    unknown = await registry.execute("send_email", {}, _ctx())
    assert unknown.denied


async def test_other_partys_claim_gets_the_same_error_as_a_missing_one(registry: ToolRegistry) -> None:
    foreign = await registry.execute("get_claim", {"case_id": "CL-3001"}, _ctx())
    missing = await registry.execute("get_claim", {"case_id": "CL-0000"}, _ctx())
    assert foreign.error == missing.error == NOT_AVAILABLE
    assert foreign.model_dump() == missing.model_dump()


async def test_party_id_sent_by_the_model_is_ignored(registry: ToolRegistry) -> None:
    result = await registry.execute("get_claim", {"case_id": "CL-3001", "party_id": "P12"}, _ctx(party="P9"))
    assert result.error == NOT_AVAILABLE


async def test_get_claim_returns_formatted_amounts_and_deadline(registry: ToolRegistry) -> None:
    result = await registry.execute("get_claim", {"case_id": "cl-2048"}, _ctx())
    assert result.ok and result.data is not None
    assert result.data["amounts"]["allowed_max_amount"]["value"] == "$1,450.00"
    assert result.data["appeal_deadline"]["days_remaining"] == 8
    assert result.case_ids == ["CL-2048"]


async def test_list_claims_returns_only_own_claims(registry: ToolRegistry) -> None:
    result = await registry.execute("list_claims", {}, _ctx(Phase.RESOLVE_INTENT))
    assert result.ok and result.data is not None
    assert {c["case_id"] for c in result.data["claims"]} == {"CL-2048", "CL-2011", "CL-1899", "CL-2102"}
    assert result.case_ids == []


async def test_followup_guidance_renders_or_falls_back(
    registry: ToolRegistry, snapshot_repo: InMemoryRepository
) -> None:
    ok = await registry.execute(
        "get_followup_guidance", {"case_id": "CL-2048", "topic": "submission_method"}, _ctx()
    )
    assert ok.data is not None and "CL-2048" in ok.data["text"]
    no_docs = await registry.execute(
        "get_followup_guidance", {"case_id": "CL-2011", "topic": "submission_method"}, _ctx()
    )
    assert (
        no_docs.data is not None
        and no_docs.data["text"] == snapshot_repo.document_guideline().followup_fallback
    )
    bad = await registry.execute("get_followup_guidance", {"case_id": "CL-2048", "topic": "nope"}, _ctx())
    assert not bad.ok
