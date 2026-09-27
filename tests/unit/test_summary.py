"""SummaryFacts, the template fallback, rendering and the SummaryWriter (SPEC §8.4.1-§8.4.2)."""

from datetime import date
from typing import Any

import pytest

from sop_agent.agent.guard import OutputGuard
from sop_agent.agent.prompt_loader import load_prompt
from sop_agent.agent.sensitive_index import SensitiveIndex, amounts_in, dates_in
from sop_agent.data.repository import InMemoryRepository
from sop_agent.domain.enums import IdentityField, Path, Phase
from sop_agent.llm.base import LLMOutputError, LLMResponse, LLMUnavailableError
from sop_agent.llm.fake import FakeLLMClient, ScriptItem
from sop_agent.memory.state import (
    ActionRecord,
    DraftSource,
    FieldValue,
    FollowUp,
    FollowUpOwner,
    SessionState,
)
from sop_agent.nlu.schema import ValueSource
from sop_agent.postprocess.summary import (
    EmailContent,
    TemplateDrafter,
    build_facts,
    draft_from,
    template_content,
)
from sop_agent.postprocess.writer import SummaryWriter
from sop_agent.sop.directive import ActionKind
from tests.builders import new_state
from tests.policy_helpers import verified

COMPANY = "Northwind Insurance"
SECRETS = ("4472", "1985-03-15", "March 15, 1985", "6505212836", "650-521-2836")


def _margaret_state() -> SessionState:
    state = verified(new_state(), "P9", Phase.POST_PROCESS)
    identity = state.memory.identity
    for name, value in ((IdentityField.DOB, "1985-03-15"), (IdentityField.ID_LAST4, "4472")):
        identity.values[name] = FieldValue(value=value, turn=1, source=ValueSource.LLM)
    log = state.memory.case_log
    log.discussed_case_ids += ["CL-2048", "CL-2011", "CL-3001"]  # CL-3001 is P12's and must be dropped
    log.questions_answered += ["why was my claim denied", "how do I send the documents"]
    log.follow_ups += [
        FollowUp(item="Submit the pathology report", owner=FollowUpOwner.CALLER),
        FollowUp(item="Appeal deadline", due_date=date(2026, 3, 18), owner=FollowUpOwner.CALLER),
    ]
    log.actions.append(ActionRecord(kind=ActionKind.TRANSFER_TO_LIVE_AGENT, turn=3, ok=False))
    state.memory.selected_path = Path.DENIAL_QUESTION
    return state


def test_facts_come_only_from_the_case_log_and_own_claims(snapshot_repo: InMemoryRepository) -> None:
    facts = build_facts(_margaret_state(), snapshot_repo, COMPANY)
    assert facts.claim_numbers == ["CL-2048", "CL-2011"]
    denied, closed = facts.outcomes
    assert denied.conclusion.startswith("Denied because the review file did not include")
    assert denied.amounts == {}
    assert closed.amounts == {"Amount paid": "$780.00", "Maximum allowed amount": "$800.00"}
    assert [f.due for f in facts.follow_ups] == [None, "March 18, 2026"]
    assert facts.discussed == ["why was my claim denied", "how do I send the documents"]
    assert facts.actions == []  # a failed transfer isn't reported as done
    assert (facts.call_date, facts.addressee) == ("March 10, 2026", "Margaret Chen")


def test_facts_never_contain_identity_values(snapshot_repo: InMemoryRepository) -> None:
    rendered = build_facts(_margaret_state(), snapshot_repo, COMPANY).as_json()
    for secret in (*SECRETS, "margaret@email.com"):
        assert secret not in rendered


def test_template_draft_renders_every_section(snapshot_repo: InMemoryRepository) -> None:
    draft = TemplateDrafter(snapshot_repo, COMPANY).draft(_margaret_state(), "margaret@email.com")
    assert draft.subject == "Summary of your call with Northwind Insurance on March 10, 2026"
    assert draft.generated_by is DraftSource.TEMPLATE and draft.to == "margaret@email.com"
    for section in (
        "What we discussed:",
        "Claim status and outcome:",
        "Next steps:",
        "Reference: CL-2048, CL-2011",
    ):
        assert section in draft.text
    assert "Submit the pathology report" in draft.text and "by March 18, 2026" in draft.text
    assert "<li>" in draft.html
    for secret in SECRETS:
        assert secret not in draft.text and secret not in draft.html


def test_html_is_escaped_and_subject_is_always_the_spec_format(snapshot_repo: InMemoryRepository) -> None:
    facts = build_facts(_margaret_state(), snapshot_repo, COMPANY)
    content = template_content(facts).model_copy(
        update={"subject": "Anything", "discussed": ["<script>alert(1)</script>"]}
    )
    draft = draft_from(content, facts, "m@e.com", DraftSource.LLM)
    assert "<script>" not in draft.html and "&lt;script&gt;" in draft.html
    assert "<script>" in draft.text  # plain text is not HTML
    assert draft.subject.startswith("Summary of your call with Northwind Insurance on")


def _writer(repo: InMemoryRepository, *items: ScriptItem) -> tuple[SummaryWriter, FakeLLMClient]:
    fake = FakeLLMClient(list(items))
    writer = SummaryWriter(
        fake,
        "claude-sonnet-test",
        load_prompt("summary.md"),
        repo,
        OutputGuard(SensitiveIndex(repo)),
        COMPANY,
    )
    return writer, fake


def _content(repo: InMemoryRepository, **changes: Any) -> EmailContent:
    return template_content(build_facts(_margaret_state(), repo, COMPANY)).model_copy(update=changes)


async def test_writer_uses_a_grounded_llm_draft(snapshot_repo: InMemoryRepository) -> None:
    content = _content(snapshot_repo, greeting="Hi Margaret,")
    writer, fake = _writer(snapshot_repo, LLMResponse(text=content.model_dump_json(), parsed=content))
    draft = await writer.draft(_margaret_state(), "margaret@email.com")
    assert draft.generated_by is DraftSource.LLM and draft.text.startswith("Hi Margaret,")
    [request] = fake.requests
    assert request.output_model is EmailContent and request.effort == "low"
    message = request.messages[-1].content
    assert isinstance(message, str) and message.startswith("FACTS:\n")
    for secret in SECRETS:
        assert secret not in message


@pytest.mark.parametrize(
    "changes",
    [
        {"outcomes": ["CL-2048: you'll receive $1,450.00 soon."]},  # G4: not a fact (the claim was denied)
        {"next_steps": ["Appeal by April 30, 2026."]},  # G4: date not in facts
        {"closing": "Your date of birth on file is March 15, 1985."},  # G3
        {"closing": "Your SSN ends in 4472."},  # G3
        {"outcomes": ["Claim CL-3001 was also reviewed."]},  # G2: another policyholder's claim
    ],
)
async def test_writer_falls_back_when_the_draft_breaks_a_rule(
    snapshot_repo: InMemoryRepository, changes: dict[str, Any]
) -> None:
    bad = _content(snapshot_repo, **changes)
    writer, _ = _writer(snapshot_repo, LLMResponse(text=bad.model_dump_json(), parsed=bad))
    draft = await writer.draft(_margaret_state(), "margaret@email.com")
    assert draft.generated_by is DraftSource.TEMPLATE
    for value in ("$1,450.00 soon", "April 30", "March 15, 1985", "4472", "CL-3001"):
        assert value not in draft.text


@pytest.mark.parametrize("error", [LLMUnavailableError("down"), LLMOutputError("refused", "refusal")])
async def test_writer_falls_back_when_the_llm_fails(
    snapshot_repo: InMemoryRepository, error: Exception
) -> None:
    writer, _ = _writer(snapshot_repo, error)
    draft = await writer.draft(_margaret_state(), "margaret@email.com")
    assert draft.generated_by is DraftSource.TEMPLATE and "CL-2048" in draft.text


async def test_every_amount_and_date_in_the_draft_comes_from_the_facts(
    snapshot_repo: InMemoryRepository,
) -> None:
    state = _margaret_state()
    facts = build_facts(state, snapshot_repo, COMPANY)
    writer, _ = _writer(snapshot_repo, LLMUnavailableError("down"))
    draft = await writer.draft(state, "margaret@email.com")
    assert amounts_in(draft.text) <= amounts_in(facts.as_json())
    assert dates_in(draft.text)[0] <= dates_in(facts.as_json())[0]
