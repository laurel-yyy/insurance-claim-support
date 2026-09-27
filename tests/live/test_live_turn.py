"""One real end-to-end turn: real extractor (Haiku 4.5) and responder (Sonnet 5). Skipped without an API key."""

from datetime import date
from pathlib import Path

import pytest

from sop_agent.agent.sensitive_index import TokenKind
from sop_agent.config import Settings
from sop_agent.container import Container
from sop_agent.domain.enums import Phase
from sop_agent.observability.logging import get_logger
from sop_agent.observability.trace import MemoryTraceSink
from sop_agent.sop.directive import EventType
from tests.helpers import SNAPSHOT_DIR

_log = get_logger(__name__)

MARGARET = (
    "I'm the policyholder. My name is Margaret Chen, policy POL-9921. I'm calling about my denied healthcare "
    "claim from January. DOB is 1985-03-15, SSN last four is 4472."
)


@pytest.mark.live
async def test_live_margaret_turn_end_to_end(tmp_path: Path) -> None:
    settings = Settings(fixtures_dir=SNAPSHOT_DIR, demo_today=date(2026, 3, 10), var_dir=tmp_path / "var")
    container = Container.build(settings)
    traces = MemoryTraceSink()
    orchestrator = container.make_orchestrator(container.make_agents(container.make_llm()), tracer=traces)
    session = orchestrator.start_session()

    result = await orchestrator.handle_turn(session.session_id, MARGARET)
    events = [e.type.value for e in result.events]
    _log.info("live turn", extra={"fields": {"reply": result.reply, "events": events}})

    kinds = {e.type for e in result.events}
    assert result.phase is Phase.PROCESS_CASE
    assert {EventType.VERIFIED, EventType.CLAIM_RESOLVED, EventType.PATH_SELECTED} <= kinds
    assert EventType.LLM_FALLBACK not in kinds, "a model call failed or the guard blocked twice"
    assert not result.fallback_used
    assert "CL-2048" in result.reply  # §8.2.5: an auto-resolved claim is named so the caller can correct it
    assert "4472" not in result.reply and "1985-03-15" not in result.reply  # G3
    other = {h.kind for h in container.services.index.find(result.reply) if "P9" not in h.parties}
    assert other <= {TokenKind.MONTH_DAY}, f"reply mentions another policyholder's data: {other}"
