"""Extractor with FakeLLMClient: hybrid merge, conversion, degraded mode and request contents."""

from typing import Any

import pytest

from sop_agent.agent.prompt_loader import load_prompt
from sop_agent.data.repository import InMemoryRepository
from sop_agent.domain.dates import DateHint
from sop_agent.domain.enums import CallerRole, ClaimStatus, IdentityField, Path, Phase
from sop_agent.llm.base import (
    LLMOutputError,
    LLMRequest,
    LLMResponse,
    LLMTimeoutError,
    LLMUnavailableError,
    Role,
)
from sop_agent.llm.fake import FakeLLMClient
from sop_agent.memory.state import ChatRole, ChatTurn, SessionState
from sop_agent.nlu.extractor import HISTORY_TURNS, Extractor
from sop_agent.nlu.schema import Confirmation, DialogAct, IdKind, NLUResult, QuestionKind, Scope, ValueSource
from sop_agent.sop.directive import PendingQuestion, PendingQuestionKind
from tests.builders import new_state
from tests.nlu_helpers import wire_payload
from tests.policy_helpers import record_tokens

F = IdentityField
MARGARET = (
    "I'm the policyholder. My name is Margaret Chen, policy POL-9921. I'm calling about my denied healthcare "
    "claim from January. DOB is 1985-03-15, SSN last four is 4472."
)


@pytest.fixture
def fake() -> FakeLLMClient:
    return FakeLLMClient()


@pytest.fixture
def extractor(snapshot_repo: InMemoryRepository, fake: FakeLLMClient) -> Extractor:
    return Extractor(
        fake, "claude-haiku-test", load_prompt("extractor.md"), snapshot_repo.document_guideline()
    )


def wire(extractor: Extractor, **fields: Any) -> LLMResponse:
    parsed = extractor.wire_model.model_validate(wire_payload(**fields))
    return LLMResponse(text=parsed.model_dump_json(), parsed=parsed)


async def extract(extractor: Extractor, text: str, state: SessionState | None = None) -> NLUResult:
    return await extractor.extract(text, state or new_state())


async def test_margaret_hybrid_merge_and_conversion(extractor: Extractor, fake: FakeLLMClient) -> None:
    fake.queue(
        wire(
            extractor,
            dialog_acts=["provide_identity", "state_need"],
            full_name="Margaret Chen",
            dob="1985-03-15",
            id_last4="4472",
            id_kind="ssn",
            policy_number="POL-9921",
            caller_role="policyholder",
            case_type="healthcare",
            claim_status="denied",
            date_month=1,
            intents=[{"path": "denial_question", "confidence": 0.7}, {"path": "none", "confidence": 0.1}],
        )
    )
    result = await extract(extractor, MARGARET)
    name = result.identity[F.FULL_NAME]
    assert (name.raw, name.source) == ("Margaret Chen", ValueSource.LLM)
    assert (result.identity[F.DOB].raw, result.identity[F.DOB].source) == ("1985-03-15", ValueSource.BOTH)
    assert result.identity[F.ID_LAST4].source is ValueSource.BOTH
    assert (result.policy_number, result.caller_role, result.id_kind) == (
        "POL-9921",
        CallerRole.POLICYHOLDER,
        IdKind.SSN,
    )
    assert (result.case_type, result.claim_status, result.date_hint) == (
        "healthcare",
        ClaimStatus.DENIED,
        DateHint(month=1),
    )
    assert [(i.path, i.confidence) for i in result.intents] == [(Path.DENIAL_QUESTION, 0.7)]
    assert result.conflicts == [] and not result.degraded
    assert result.text == MARGARET


async def test_regex_wins_on_disagreement_and_records_a_conflict(
    extractor: Extractor, fake: FakeLLMClient
) -> None:
    fake.queue(wire(extractor, dob="1985-05-13", id_last4="4471", policy_number="POL-9912"))
    result = await extract(extractor, MARGARET)
    assert (result.identity[F.DOB].raw, result.identity[F.DOB].source) == ("1985-03-15", ValueSource.REGEX)
    assert result.identity[F.ID_LAST4].raw == "4472"
    assert result.policy_number == "POL-9921"
    assert set(result.conflicts) == {"dob", "id_last4", "policy_number"}


async def test_llm_only_values_are_normalized_and_invalid_ones_stay_raw(
    extractor: Extractor, fake: FakeLLMClient
) -> None:
    fake.queue(wire(extractor, full_name="Ma Tian", dob="March 15, 1985", id_last4="47"))
    result = await extract(extractor, "my name is Ma Tian, born in the spring")
    assert result.identity[F.DOB].raw == "1985-03-15"
    assert result.identity[F.ID_LAST4].raw == "47"  # memory merge will emit IDENTITY_FIELD_INVALID


async def test_empty_wire_values_become_none(extractor: Extractor, fake: FakeLLMClient) -> None:
    fake.queue(wire(extractor, questions=[{"text": " ", "kind": "account"}]))
    result = await extract(extractor, "hello")
    assert result.identity == {}
    assert (result.policy_number, result.case_id, result.case_type, result.date_hint) == (
        None,
        None,
        None,
        None,
    )
    assert (result.confirmation, result.claim_status, result.id_kind) == (None, None, None)
    assert result.questions == []


async def test_labels_and_fields_convert_to_domain(extractor: Extractor, fake: FakeLLMClient) -> None:
    fake.queue(
        wire(
            extractor,
            confirmation="yes",
            summary_email="alt@work.example",
            questions=[{"text": "what is an EOB", "kind": "general"}],
            followup_topics=["submission_method"],
            emotion="anxious",
            emotion_intensity=9,
            scope="mixed",
            date_year=2026,
            date_month=13,
            date_day=12,
        )
    )
    result = await extract(extractor, "sure")
    assert result.confirmation is Confirmation.YES and result.summary_email == "alt@work.example"
    assert [(q.text, q.kind) for q in result.questions] == [("what is an EOB", QuestionKind.GENERAL)]
    assert result.followup_topics == ["submission_method"]
    assert result.emotion_intensity == 3 and result.scope is Scope.MIXED
    assert result.date_hint == DateHint(year=2026, day=12)


async def test_one_failure_then_success_is_not_degraded(extractor: Extractor, fake: FakeLLMClient) -> None:
    fake.queue(LLMTimeoutError("t"), wire(extractor, dialog_acts=["greeting"]))
    result = await extract(extractor, "hi")
    assert not result.degraded and result.dialog_acts == [DialogAct.GREETING]
    assert len(fake.requests) == 2


async def test_two_failures_fall_back_to_regex_only(extractor: Extractor, fake: FakeLLMClient) -> None:
    fake.queue(LLMUnavailableError("down"), LLMOutputError("bad", "refusal"))
    result = await extract(extractor, MARGARET)
    assert result.degraded
    assert result.dialog_acts == [DialogAct.OTHER] and result.scope is Scope.IN_SCOPE
    assert {f: m.raw for f, m in result.identity.items()} == {F.DOB: "1985-03-15", F.ID_LAST4: "4472"}
    assert all(m.source is ValueSource.REGEX for m in result.identity.values())
    assert result.policy_number == "POL-9921"
    assert len(fake.requests) == 2


async def test_response_without_structured_output_counts_as_a_failure(
    extractor: Extractor, fake: FakeLLMClient
) -> None:
    fake.queue(LLMResponse(text="{}"), LLMResponse(text="{}"))
    assert (await extract(extractor, "hi")).degraded


def _state_with_history(phase: Phase) -> SessionState:
    state = new_state(phase)
    for i in range(1, 5):
        state.history.append(ChatTurn(role=ChatRole.CALLER, text=f"caller message {i}", turn=i))
        state.history.append(ChatTurn(role=ChatRole.AGENT, text=f"agent reply {i}", turn=i))
    state.history.append(ChatTurn(role=ChatRole.CALLER, text="yes please", turn=5))
    return state


async def test_request_uses_rendered_prompt_schema_and_history(
    extractor: Extractor, fake: FakeLLMClient
) -> None:
    fake.queue(wire(extractor))
    state = _state_with_history(Phase.POST_PROCESS)
    state.pending_question = PendingQuestion(
        kind=PendingQuestionKind.OFFER_SUMMARY_EMAIL, asked_in_phase=Phase.POST_PROCESS, asked_turn=3
    )
    await extract(extractor, "yes please", state)
    [request] = fake.requests
    assert "TODAY: 2026-03-10" in request.system
    assert "CURRENT_PHASE: POST_PROCESS" in request.system
    assert "PENDING_QUESTION: OFFER_SUMMARY_EMAIL" in request.system
    assert "LAST_AGENT_MESSAGE: agent reply 4" in request.system
    assert "- submission_method (e.g." in request.system
    assert "{" + "today}" not in request.system
    assert request.output_model is extractor.wire_model
    assert request.max_tokens == 1500 and request.effort is None
    assert request.messages[-1].role is Role.USER and request.messages[-1].content == "yes please"
    assert request.messages[0].role is Role.USER
    assert len(request.messages) <= HISTORY_TURNS + 1
    assert [m.content for m in request.messages].count("yes please") == 1


def _render(request: LLMRequest) -> str:
    return request.system + "\n" + "\n".join(str(m.content) for m in request.messages)


async def test_inv2_requests_before_verification_contain_no_record_data(
    snapshot_repo: InMemoryRepository, extractor: Extractor, fake: FakeLLMClient
) -> None:
    fake.queue(wire(extractor))
    await extract(extractor, "Hi, I need help with a claim", new_state())
    [request] = fake.requests
    names = {h.name.casefold() for h in snapshot_repo.policyholders()}
    names |= {r.rep_name.casefold() for r in snapshot_repo.representatives()}
    rendered = _render(request).casefold()
    leaked = sorted(t for t in record_tokens(snapshot_repo) | names if t in rendered)
    assert not leaked, f"extractor request leaks record data: {leaked}"
