"""The eval assertions themselves, checked offline (no LLM)."""

from pathlib import Path

import yaml

from evals.assertions import CHECKS, TurnObservation, check_turn
from sop_agent.agent.orchestrator import TurnResult
from sop_agent.agent.sensitive_index import SensitiveIndex
from sop_agent.data.repository import InMemoryRepository
from sop_agent.domain.enums import IdentityStatus, Phase
from sop_agent.sop.directive import Event, EventType
from tests.builders import new_state


def _obs(
    repo: InMemoryRepository, reply: str, verified: bool = False, caller: tuple[str, ...] = ()
) -> TurnObservation:
    state = new_state(Phase.VERIFY_ID)
    if verified:
        state.memory.identity.status = IdentityStatus.VERIFIED
    result = TurnResult(
        session_id="s",
        reply=reply,
        phase=state.phase,
        verify_stage=None,
        quick_replies=["Send the summary"],
        events=[Event(type=EventType.OFF_TOPIC_DECLINED, turn=1, phase=Phase.VERIFY_ID)],
    )
    return TurnObservation(result, state, list(caller), 0, SensitiveIndex(repo))


def test_passing_and_failing_assertions(snapshot_repo: InMemoryRepository) -> None:
    obs = _obs(snapshot_repo, "Please share your date of birth.")
    assert check_turn(obs, {"phase": "VERIFY_ID", "reply_contains_any": ["birth"], "email_sent": False}) == []
    failures = check_turn(
        obs, {"phase": "ENDED", "events_include": ["VERIFIED"], "reply_contains_any_2": ["x"]}
    )
    assert len(failures) == 3 and failures[0].startswith("phase:")


def test_no_record_leak_uses_g1_logic(snapshot_repo: InMemoryRepository) -> None:
    assert check_turn(_obs(snapshot_repo, "Claim CL-2048 is denied."), {"no_record_leak": True})
    assert (
        check_turn(_obs(snapshot_repo, "Noted CL-2048.", caller=("about CL-2048",)), {"no_record_leak": True})
        == []
    )
    assert check_turn(_obs(snapshot_repo, "Claim CL-2048.", verified=True), {"no_record_leak": True}) == []


def test_unknown_assertion_is_reported(snapshot_repo: InMemoryRepository) -> None:
    assert check_turn(_obs(snapshot_repo, "hi"), {"judge": 5}) == ["judge: unknown assertion 'judge'"]


def test_every_scenario_uses_only_known_assertions() -> None:
    known = set(CHECKS)
    for path in Path("evals/scenarios").glob("*.yaml"):
        spec = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert spec["turns"], path.name
        for turn in spec["turns"]:
            for name in turn.get("expect", {}):
                assert name in known or name.startswith("reply_contains_any"), f"{path.name}: {name}"


def test_all_required_scenarios_exist() -> None:
    required = {
        "margaret_happy_path", "margaret_skip_email", "frustrated_before_verification", "refuse_id_use_phone_email",
        "partial_answers_and_clarifications", "failed_verification_lockout", "cross_record_mix", "alias_name_yaven_li",
        "national_id_ma_tian", "representative_consent_approved", "representative_consent_timeout",
        "representative_not_authorized", "off_topic_retries", "prompt_injection", "live_agent_request_midflow",
        "ungrounded_question", "document_alternatives_exhausted", "medical_advice",
    }  # fmt: skip
    assert {p.stem for p in Path("evals/scenarios").glob("*.yaml")} == required
