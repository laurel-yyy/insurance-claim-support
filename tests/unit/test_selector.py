"""ClaimSelector: bounded choice, validated output, candidate IDs only in message content (SPEC §8.2.6)."""

import json

import pytest

from sop_agent.agent.prompt_loader import load_prompt
from sop_agent.domain.enums import Path
from sop_agent.llm.anthropic_client import strict_schema
from sop_agent.llm.base import LLMUnavailableError, Role
from sop_agent.llm.fake import FakeLLMClient, ScriptItem
from sop_agent.nlu.selector import ClaimSelector, SelectorCandidate
from sop_agent.nlu.wire import PathOrNone, SelectorWire

CANDIDATES = [
    SelectorCandidate("CL-9101", "healthcare, created 2026-01-08, denied: X-ray images missing"),
    SelectorCandidate("CL-9102", "healthcare, created 2026-01-21, denied: referral missing"),
]
PATHS = [Path.DENIAL_QUESTION, Path.DOCUMENT_SUBMISSION]


def _selector(*items: ScriptItem) -> tuple[ClaimSelector, FakeLLMClient]:
    fake = FakeLLMClient(list(items))
    return ClaimSelector(fake, "claude-haiku-test", load_prompt("selector.md")), fake


async def test_valid_choice_is_returned() -> None:
    selector, _ = _selector(
        SelectorWire(case_id="cl-9102", path=PathOrNone.DOCUMENT_SUBMISSION, confidence=0.9)
    )
    choice = await selector.select("the referral one", CANDIDATES, PATHS)
    assert (choice.case_id, choice.path) == ("CL-9102", Path.DOCUMENT_SUBMISSION)


@pytest.mark.parametrize("case_id", ["CL-2048", "", "the second"])
async def test_case_id_outside_candidates_is_dropped(case_id: str) -> None:
    selector, _ = _selector(SelectorWire(case_id=case_id, path=PathOrNone.NONE, confidence=0.9))
    assert (await selector.select("that one", CANDIDATES, PATHS)).case_id == ""


async def test_disallowed_path_is_dropped() -> None:
    selector, _ = _selector(SelectorWire(case_id="CL-9101", path=PathOrNone.HUMAN_HANDOFF, confidence=0.9))
    choice = await selector.select("the x-ray one", CANDIDATES, PATHS)
    assert (choice.case_id, choice.path) == ("CL-9101", None)


async def test_llm_failure_is_an_empty_choice() -> None:
    selector, _ = _selector(LLMUnavailableError("down"))
    choice = await selector.select("the first", CANDIDATES, PATHS)
    assert (choice.case_id, choice.path) == ("", None)


async def test_no_candidates_makes_no_call() -> None:
    selector, fake = _selector()
    assert (await selector.select("x", [], PATHS)).case_id == ""
    assert fake.requests == []


async def test_candidate_ids_are_in_the_message_and_never_in_the_schema() -> None:
    selector, fake = _selector(SelectorWire(case_id="", path=PathOrNone.NONE, confidence=0.0))
    await selector.select("whichever mentions the referral", CANDIDATES, PATHS)
    [request] = fake.requests
    [message] = request.messages
    assert message.role is Role.USER
    assert "1. CL-9101" in str(message.content) and "2. CL-9102" in str(message.content)
    assert "ALLOWED_PATHS: denial_question, document_submission" in str(message.content)
    assert request.output_model is SelectorWire and request.max_tokens == 400
    schema = json.dumps(strict_schema(SelectorWire))
    assert "CL-" not in schema
    assert "whichever mentions the referral" not in request.system  # caller text only in the user message
    assert "whichever mentions the referral" in str(message.content)
