"""End-to-end flows with FakeLLMClient (SPEC §15.2): Margaret, representatives, INV-2 scan, fault injection."""

import json
from pathlib import Path
from typing import Any

import pytest

from sop_agent.agent.sensitive_index import SensitiveIndex
from sop_agent.domain.enums import Phase, VerifyStage
from sop_agent.llm.base import LLMRequest, LLMResponse, LLMUnavailableError
from sop_agent.sop.directive import EventType
from tests.helpers import SNAPSHOT_DIR
from tests.integration.harness import SAFE_REPLY, Harness, build_harness

MARGARET = (
    "I'm the policyholder. My name is Margaret Chen, policy POL-9921. I'm calling about my denied healthcare "
    "claim from January. DOB is 1985-03-15, SSN last four is 4472."
)
MARGARET_NLU: dict[str, Any] = {
    "dialog_acts": ["provide_identity", "state_need"],
    "full_name": "Margaret Chen",
    "dob": "1985-03-15",
    "id_last4": "4472",
    "id_kind": "ssn",
    "policy_number": "POL-9921",
    "caller_role": "policyholder",
    "case_type": "healthcare",
    "claim_status": "denied",
    "date_month": 1,
    "intents": [{"path": "denial_question", "confidence": 0.7}],
}
DAVID = "Hi, this is David Chen. I'm calling for my mom, Margaret Chen. Her birthday is March 15, 1985 and her phone is 650-521-2836."
DAVID_NLU: dict[str, Any] = {
    "dialog_acts": ["provide_identity", "state_need"],
    "full_name": "Margaret Chen",
    "dob": "1985-03-15",
    "phone": "6505212836",
    "caller_role": "authorized_representative",
    "representative_name": "David Chen",
    "relationship_to_policyholder": "child",
    "case_type": "healthcare",
    "claim_status": "denied",
    "date_month": 1,
}


@pytest.fixture
def h(tmp_path: Path) -> Harness:
    return build_harness(tmp_path, SNAPSHOT_DIR)


def _types(result: Any) -> list[EventType]:
    return [e.type for e in result.events]


def _index(h: Harness) -> SensitiveIndex:
    return h.container.services.index


def _assert_clean(h: Harness, requests: list[LLMRequest], said_upto: int) -> None:
    for request in requests:
        leaked = h.unsaid_record_tokens(request, _index(h), h.caller_texts[:said_upto])
        assert not leaked, f"pre-verification LLM request contains record data: {leaked}"


async def test_margaret_end_to_end(h: Harness) -> None:
    greeting = h.start()
    assert greeting.phase is Phase.VERIFY_ID and "verify your identity" in greeting.reply

    first = await h.say(MARGARET, **MARGARET_NLU)
    assert first.phase is Phase.PROCESS_CASE
    assert {
        EventType.HINT_STORED,
        EventType.VERIFIED,
        EventType.CLAIM_RESOLVED,
        EventType.PATH_SELECTED,
    } <= set(_types(first))
    assert first.phase_trail == [Phase.RESOLVE_INTENT, Phase.PROCESS_CASE]
    extractor_request, responder_request = h.turn_requests[0]
    _assert_clean(h, [extractor_request], 1)  # runs before verification
    assert "CL-2048" in responder_request.system and "$1,450.00" in responder_request.system

    second = await h.say(
        "How do I send them? I might not be able to get the original pathology report.",
        dialog_acts=["ask_question"],
        intents=[{"path": "document_submission", "confidence": 0.9}],
        followup_topics=["submission_method"],
        questions=[{"text": "how do I send them", "kind": "account"}],
        unavailable_documents=["pathology report"],
    )
    assert second.phase is Phase.PROCESS_CASE
    responder_system = h.turn_requests[1][-1].system
    assert '"alternatives"' in responder_system and "replacement copy" in responder_system
    assert "submission_method" in responder_system

    third = await h.say("Thanks, that's all.", dialog_acts=["done", "thanks"])
    assert third.phase is Phase.POST_PROCESS
    assert third.quick_replies[:2] == ["Send the summary", "No thanks"]
    assert "margaret@email.com" not in h.turn_requests[2][-1].system  # only the masked address is grounded

    fourth = await h.say("Yes, please send it.", dialog_acts=["confirm"], confirmation="yes")
    assert fourth.phase is Phase.ENDED
    assert {EventType.EMAIL_SENT, EventType.SESSION_ENDED} <= set(_types(fourth))
    [email] = h.container.services.outbox.outbox(h.session_id)
    assert email.to == "margaret@email.com" and "CL-2048" in email.text
    assert "4472" not in email.text and "1985-03-15" not in email.text

    closed = await h.say("one more thing")
    assert closed.reply.startswith("This conversation has ended")
    assert h.turn_requests[-1] == []  # terminal phases make no LLM calls


async def test_traces_are_masked(h: Harness) -> None:
    h.start()
    await h.say(MARGARET, **MARGARET_NLU)
    rendered = json.dumps(h.traces.traces)
    for secret in ("1985-03-15", "4472", "margaret@email.com"):
        assert secret not in rendered
    assert h.traces.traces[0]["phase_after"] == "PROCESS_CASE"


async def test_representative_default_scenario_completes_consent(h: Harness) -> None:
    h.start("default")
    first = await h.say(DAVID, **DAVID_NLU)
    assert (first.phase, first.verify_stage) == (Phase.VERIFY_ID, VerifyStage.CONSENT)
    assert EventType.IDENTITY_VERIFIED in _types(first) and EventType.VERIFIED not in _types(first)
    second = await h.say("Yes, please text her.", dialog_acts=["confirm"], confirmation="yes")
    assert {EventType.CONSENT_REQUESTED, EventType.CONSENT_STATUS} <= set(_types(second))
    assert second.phase is Phase.VERIFY_ID
    third = await h.say("Check again")
    assert EventType.CONSENT_APPROVED in _types(third) and EventType.VERIFIED in _types(third)
    assert third.phase is Phase.PROCESS_CASE  # hints from the first turn resolved CL-2048
    for turn, requests in enumerate(h.turn_requests[:2], start=1):
        _assert_clean(h, requests, turn)
    _assert_clean(h, h.turn_requests[2][:1], 3)
    assert list((h.container.settings.var_dir / "sms").glob("*.json"))


async def test_representative_timeout_ends_with_a_live_agent_offer(h: Harness) -> None:
    h.start("timeout")
    await h.say(DAVID, **DAVID_NLU)
    await h.say("Yes", dialog_acts=["confirm"], confirmation="yes")
    results = [await h.say("Check again") for _ in range(4)]
    last = results[-1]
    assert EventType.CONSENT_TIMEOUT in _types(last)
    assert EventType.LIVE_AGENT_OFFERED in _types(last)
    assert last.phase is Phase.VERIFY_ID
    assert all(EventType.VERIFIED not in _types(r) for r in results)
    for turn, requests in enumerate(h.turn_requests, start=1):
        _assert_clean(h, requests, turn)  # never verified, so every request must be clean


async def test_extractor_failure_degrades_but_the_turn_still_answers(h: Harness) -> None:
    h.start()
    h.nlu_queue.extend([LLMUnavailableError("down"), LLMUnavailableError("down")])
    result = await h.orchestrator.handle_turn(h.session_id, "DOB is 1985-03-15")
    assert EventType.LLM_FALLBACK in _types(result)
    assert result.reply == SAFE_REPLY and result.phase is Phase.VERIFY_ID


async def test_responder_failure_uses_the_fallback_and_leaks_nothing(h: Harness) -> None:
    h.start()

    def broken(_: LLMRequest) -> LLMUnavailableError:
        return LLMUnavailableError("down")

    h.responder = broken
    result = await h.say("My name is Margaret Chen", full_name="Margaret Chen")
    assert result.fallback_used and EventType.LLM_FALLBACK in _types(result)
    assert "verify your identity" in result.reply
    assert not {h.kind for h in _index(h).find(result.reply) if h.kind.value != "name"}


async def test_guard_blocks_a_leak_then_falls_back(h: Harness) -> None:
    h.start()
    h.responder = lambda _: LLMResponse(text="Your claim CL-2048 was denied for missing records.")
    result = await h.say(
        "Why was my claim denied?", questions=[{"text": "why was my claim denied", "kind": "account"}]
    )
    blocked = [e for e in result.events if e.type is EventType.GUARD_BLOCKED]
    assert blocked and all(e.data["rule"] == "G1" for e in blocked)
    assert result.fallback_used and "CL-2048" not in result.reply
    regenerate = h.turn_requests[0][-1]
    assert "<guard_note>" in regenerate.system and "CL-2048" not in regenerate.system


async def test_guard_accepts_a_clean_regeneration(h: Harness) -> None:
    h.start()
    replies = iter(["Claim CL-2048 is denied.", "I can look into that right after I verify your identity."])
    h.responder = lambda _: LLMResponse(text=next(replies))
    result = await h.say("Why was my claim denied?")
    assert not result.fallback_used
    assert result.reply == "I can look into that right after I verify your identity."
    assert EventType.GUARD_BLOCKED in _types(result)


async def test_failed_email_send_stays_in_post_process_and_offers_retry(h: Harness) -> None:
    h.start()
    await h.say(MARGARET, **MARGARET_NLU)
    await h.say("That's all", dialog_acts=["done"])
    outbox_dir = h.container.settings.var_dir / "outbox"
    outbox_dir.parent.mkdir(parents=True, exist_ok=True)
    outbox_dir.write_text("blocks the outbox directory", encoding="utf-8")
    result = await h.say("Yes, send it", dialog_acts=["confirm"], confirmation="yes")
    assert result.phase is Phase.POST_PROCESS
    assert EventType.ACTION_FAILED in _types(result) and EventType.EMAIL_SENT not in _types(result)
    assert "Try again" in result.quick_replies


async def test_live_agent_request_escalates_and_writes_a_ticket(h: Harness) -> None:
    h.start()
    result = await h.say(
        "Let me talk to a person", requests_live_agent=True, dialog_acts=["request_live_agent"]
    )
    assert result.phase is Phase.ESCALATED and EventType.ESCALATED in _types(result)
    tickets = list((h.container.settings.var_dir / "handoffs").glob("*.json"))
    assert len(tickets) == 1 and '"party_id": null' in tickets[0].read_text(encoding="utf-8")
    after = await h.say("hello?")
    assert after.reply == "A member of our claims team will be with you shortly."
