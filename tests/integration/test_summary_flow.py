"""Summary email wired end to end: draft on entry, send the draft, C4 reuse, C7 re-draft."""

from pathlib import Path

import pytest

from sop_agent.domain.enums import Phase
from sop_agent.llm.base import LLMUnavailableError
from sop_agent.memory.state import DraftSource, EmailDraft
from sop_agent.postprocess.summary import EmailContent
from sop_agent.sop.directive import EventType
from tests.helpers import SNAPSHOT_DIR
from tests.integration.harness import Harness, build_harness
from tests.integration.test_end_to_end import MARGARET, MARGARET_NLU


@pytest.fixture
def h(tmp_path: Path) -> Harness:
    return build_harness(tmp_path, SNAPSHOT_DIR)


def _summary_requests(h: Harness) -> list[int]:
    return [i for i, turn in enumerate(h.turn_requests) for r in turn if r.output_model is EmailContent]


async def _to_post_process(h: Harness) -> None:
    h.start()
    await h.say(MARGARET, **MARGARET_NLU)
    await h.say(
        "Why exactly was it denied?",
        questions=[{"text": "why exactly was it denied", "kind": "account"}],
        intents=[{"path": "denial_question", "confidence": 0.9}],
    )
    await h.say("That's all, thanks", dialog_acts=["done", "thanks"])


def _draft(h: Harness) -> EmailDraft | None:
    return h.container.services.store.get(h.session_id).email_draft


def _required_draft(h: Harness) -> EmailDraft:
    draft = _draft(h)
    assert draft is not None, "expected a summary draft"
    return draft


async def test_entering_post_process_drafts_once_and_grounds_the_draft(h: Harness) -> None:
    await _to_post_process(h)
    assert _summary_requests(h) == [2]  # only on entering POST_PROCESS, never before verification
    draft = _draft(h)
    assert draft is not None and draft.generated_by is DraftSource.LLM
    assert "why exactly was it denied" in draft.text and "CL-2048" in draft.text
    responder_system = h.turn_requests[2][-1].system
    assert '"summary_draft"' in responder_system and draft.subject in responder_system
    drafted = [e for e in h.traces.traces[-1]["events"] if e["type"] == EventType.SUMMARY_DRAFTED.value]
    assert drafted and drafted[0]["data"] == {"generated_by": "llm"}


async def test_the_sent_email_is_the_draft(h: Harness) -> None:
    await _to_post_process(h)
    draft = _required_draft(h)
    await h.say("What's in it?", questions=[{"text": "what's in the summary", "kind": "process"}])  # C5
    assert len(_summary_requests(h)) == 1  # a later POST_PROCESS turn reuses the draft
    result = await h.say("Yes please", dialog_acts=["confirm"], confirmation="yes")
    assert result.phase is Phase.ENDED
    [email] = h.container.services.outbox.outbox(h.session_id)
    assert (email.subject, email.text, email.html) == (draft.subject, draft.text, draft.html)
    assert len(_summary_requests(h)) == 1  # sending doesn't redraft


async def test_confirmed_new_address_reuses_the_same_content(h: Harness) -> None:
    await _to_post_process(h)
    draft = _required_draft(h)
    await h.say("Send it to my work email", summary_email="mchen@work.com")
    await h.say("Yes, that one", dialog_acts=["confirm"], confirmation="yes")
    [email] = h.container.services.outbox.outbox(h.session_id)
    assert email.to == "mchen@work.com" and email.text == draft.text


async def test_c7_new_need_then_returning_redrafts_and_asks_again(h: Harness) -> None:
    await _to_post_process(h)
    first = _required_draft(h)
    moved = await h.say(
        "Actually, what about my auto claim?",
        case_type="auto",
        claim_status="open",
        intents=[{"path": "status_inquiry", "confidence": 0.9}],
        questions=[{"text": "what about my auto claim", "kind": "account"}],
    )
    assert moved.phase is Phase.PROCESS_CASE and _draft(h) is None
    back = await h.say("OK, that's all", dialog_acts=["done"])
    assert back.phase is Phase.POST_PROCESS and EventType.EMAIL_OFFERED in {e.type for e in back.events}
    second = _draft(h)
    assert second is not None and "CL-2102" in second.text and second.text != first.text
    assert h.container.services.outbox.outbox(h.session_id) == []


async def test_writer_failure_uses_the_template_and_the_flow_continues(h: Harness) -> None:
    h.summary = lambda _: LLMUnavailableError("down")
    await _to_post_process(h)
    draft = _draft(h)
    assert draft is not None and draft.generated_by is DraftSource.TEMPLATE
    result = await h.say("Yes", dialog_acts=["confirm"], confirmation="yes")
    assert result.phase is Phase.ENDED
    [email] = h.container.services.outbox.outbox(h.session_id)
    assert "4472" not in email.text and "1985-03-15" not in email.text
