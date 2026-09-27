"""ContextBuilder: phase-scoped grounding, and the INV-2 structural guarantee (SPEC §9.1.3)."""

from typing import Any

import pytest

from sop_agent.agent import context as context_module
from sop_agent.agent.context import Builder, ContextBuilder
from sop_agent.agent.sensitive_index import SensitiveIndex
from sop_agent.data.repository import InMemoryRepository
from sop_agent.domain.enums import IdentityField, Path, Phase
from sop_agent.memory.state import FieldValue, SessionState
from sop_agent.nlu.schema import ValueSource
from sop_agent.sop.directive import GroundingRequest, Observations, TurnDirective
from sop_agent.sop.phases import PHASES, RECORD_SCOPES, ContextScope
from tests.builders import new_state, nlu
from tests.policy_helpers import engine, margaret_message, verified

FAQ = "What is an EOB? A statement after a claim is processed."


@pytest.fixture
def builder(snapshot_repo: InMemoryRepository) -> ContextBuilder:
    return ContextBuilder(snapshot_repo, FAQ)


def _directive(repo: InMemoryRepository, state: SessionState) -> TurnDirective:
    return engine(repo).decide(state, nlu(), Observations()).directive


def test_inv2_verify_id_grounding_contains_no_record_tokens(
    snapshot_repo: InMemoryRepository, builder: ContextBuilder
) -> None:
    state = new_state()
    state.memory.identity.values[IdentityField.FULL_NAME] = FieldValue(
        value="margaret chen", turn=1, source=ValueSource.LLM
    )
    state.memory.identity.values[IdentityField.DOB] = FieldValue(
        value="1985-03-15", turn=1, source=ValueSource.LLM
    )
    state.memory.selected_case_id = "CL-2048"  # even a stray selection can't pull claim data in
    grounding = builder.build(state, _directive(snapshot_repo, state))
    assert set(grounding.scopes).isdisjoint(RECORD_SCOPES)
    hits = SensitiveIndex(snapshot_repo).find(grounding.as_json())
    assert hits == [], f"VERIFY_ID grounding leaks: {[h.kind for h in hits]}"
    for value in ("1985-03-15", "margaret chen"):
        assert value not in grounding.as_json().casefold()


def test_builder_reads_only_the_phases_scopes(
    snapshot_repo: InMemoryRepository, builder: ContextBuilder, monkeypatch: pytest.MonkeyPatch
) -> None:
    called: list[ContextScope] = []

    def recording(scope: ContextScope, fn: Builder) -> Builder:
        def wrapped(b: ContextBuilder, s: SessionState, d: TurnDirective) -> dict[str, Any]:
            called.append(scope)
            return fn(b, s, d)

        return wrapped

    for scope, fn in dict(context_module.BUILDERS).items():
        monkeypatch.setitem(context_module.BUILDERS, scope, recording(scope, fn))
    for phase in (Phase.VERIFY_ID, Phase.RESOLVE_INTENT, Phase.PROCESS_CASE, Phase.POST_PROCESS):
        called.clear()
        state = verified(new_state(), "P9", phase) if phase is not Phase.VERIFY_ID else new_state()
        builder.build(state, _directive(snapshot_repo, new_state()))
        assert set(called) == set(PHASES[phase].context_scopes)


def test_sop_status_has_field_statuses_but_no_values(
    snapshot_repo: InMemoryRepository, builder: ContextBuilder
) -> None:
    state = new_state()
    state.memory.identity.values[IdentityField.DOB] = FieldValue(
        value="1985-03-15", turn=1, source=ValueSource.LLM
    )
    state.memory.identity.declined.add(IdentityField.ID_LAST4)
    status = builder.build(state, _directive(snapshot_repo, state)).data["sop_status"]
    assert status["identity_checklist"]["dob"] == "provided"
    assert status["identity_checklist"]["id_last4"] == "declined"
    assert status["identity_checklist"]["email"] == "missing"


def test_party_profile_has_masked_email_and_no_dob_id_or_phone(
    snapshot_repo: InMemoryRepository, builder: ContextBuilder
) -> None:
    state = verified(new_state(), "P9", Phase.RESOLVE_INTENT)
    rendered = builder.build(state, _directive(snapshot_repo, state)).as_json()
    assert "m\N{BULLET}" in rendered and "margaret@email.com" not in rendered
    for secret in ("1985-03-15", "4472", "6505212836", "650"):
        assert secret not in rendered


def test_process_case_grounding_has_formatted_amounts_deadline_and_guidance(
    snapshot_repo: InMemoryRepository,
) -> None:
    eng = engine(snapshot_repo)
    decision = eng.decide(new_state(), margaret_message(), Observations())
    builder = ContextBuilder(snapshot_repo, FAQ)
    data = builder.build(decision.state, decision.directive).data
    claim = data["selected_claim"]
    assert claim["case_id"] == "CL-2048"
    assert claim["amounts"]["allowed_max_amount"]["value"] == "$1,450.00"
    assert claim["amounts"]["net_pay"]["meaning"]
    assert claim["appeal_deadline"] == {"date": "2026-03-18", "state": "open", "days_remaining": 8}
    guidance = data["claim_guidance"]
    assert [d["name"] for d in guidance["documents"]] == ["pathology report", "office note"]
    assert "missing_required_material_alternatives" in guidance["background_topics"]
    assert data["general_kb"]["faq"] == FAQ


def test_claim_guidance_renders_requested_topics_and_alternatives(
    snapshot_repo: InMemoryRepository, builder: ContextBuilder
) -> None:
    state = verified(new_state(), "P9", Phase.PROCESS_CASE)
    state.memory.selected_case_id, state.memory.selected_path = "CL-2048", Path.DOCUMENT_SUBMISSION
    directive = _directive(snapshot_repo, new_state()).model_copy(
        update={
            "grounding": GroundingRequest(
                topics=["submission_method"],
                alternatives_for=["pathology report"],
                use_followup_fallback=True,
            )
        }
    )
    guidance = builder.build(state, directive).data["claim_guidance"]
    assert "CL-2048" in guidance["followup_topics"]["submission_method"]
    assert set(guidance["alternatives"]) == {"pathology report"}
    assert guidance["followup_fallback"]


def test_selected_claim_of_another_party_is_never_grounded(
    snapshot_repo: InMemoryRepository, builder: ContextBuilder
) -> None:
    state = verified(new_state(), "P9", Phase.PROCESS_CASE)
    state.memory.selected_case_id = "CL-3001"  # P12's claim
    data = builder.build(state, _directive(snapshot_repo, new_state())).data
    assert data["selected_claim"] == {} and data["claim_guidance"] == {}
