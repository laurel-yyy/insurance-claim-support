"""One real SummaryWriter call on Margaret-like facts. Skipped without an API key (env or .env)."""

from datetime import date
from pathlib import Path

import pytest

from sop_agent.config import Settings
from sop_agent.container import Container
from sop_agent.domain.enums import Phase
from sop_agent.memory.state import DraftSource, FollowUp, FollowUpOwner
from sop_agent.observability.logging import get_logger
from tests.builders import new_state
from tests.helpers import SNAPSHOT_DIR
from tests.policy_helpers import verified

_log = get_logger(__name__)


@pytest.mark.live
async def test_live_summary_writer_produces_a_grounded_draft(tmp_path: Path) -> None:
    settings = Settings(fixtures_dir=SNAPSHOT_DIR, demo_today=date(2026, 3, 10), var_dir=tmp_path / "var")
    container = Container.build(settings)
    writer = container.make_summary_writer(container.make_llm())
    state = verified(new_state(), "P9", Phase.POST_PROCESS)
    log = state.memory.case_log
    log.discussed_case_ids.append("CL-2048")
    log.questions_answered += ["why was my claim denied", "how do I send the documents"]
    log.follow_ups += [
        FollowUp(item="Submit the pathology report", owner=FollowUpOwner.CALLER),
        FollowUp(item="Submit the office note", owner=FollowUpOwner.CALLER),
        FollowUp(item="Appeal deadline", due_date=date(2026, 3, 18), owner=FollowUpOwner.CALLER),
    ]

    draft = await writer.draft(state, "margaret@email.com")
    _log.info(
        "live summary", extra={"fields": {"generated_by": draft.generated_by.value, "text": draft.text}}
    )

    assert draft.generated_by is DraftSource.LLM, "the writer failed or its draft was blocked by the guard"
    assert draft.subject == "Summary of your call with Northwind Insurance on March 10, 2026"
    assert "CL-2048" in draft.text
    for secret in ("4472", "1985-03-15", "6505212836"):
        assert secret not in draft.text
