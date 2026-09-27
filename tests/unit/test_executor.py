"""ActionExecutor and the mock services (INV-5)."""

from datetime import date
from pathlib import Path

import pytest

from sop_agent.data.repository import InMemoryRepository
from sop_agent.domain.clock import FixedClock
from sop_agent.domain.enums import AuthorizationStatus, CallerRole, ConsentStatus, EscalationReason, Phase
from sop_agent.memory.state import SessionState
from sop_agent.postprocess.summary import TemplateDrafter
from sop_agent.sop.directive import ActionKind, EventType, PlannedAction
from sop_agent.tools.consent import ScenarioConsentService
from sop_agent.tools.email import MockOutbox
from sop_agent.tools.executor import ActionExecutor
from sop_agent.tools.handoff import LiveAgentHandoff
from tests.builders import new_state
from tests.helpers import merged_repository
from tests.policy_helpers import verified


def _executor(repo: InMemoryRepository, tmp_path: Path) -> ActionExecutor:
    return ActionExecutor(
        repo=repo,
        clock=FixedClock(date(2026, 3, 10)),
        consent=ScenarioConsentService(repo, sms_dir=tmp_path / "sms"),
        outbox=MockOutbox(tmp_path / "outbox"),
        handoff=LiveAgentHandoff(tmp_path / "handoffs"),
        drafter=TemplateDrafter(repo, "Northwind Insurance"),
    )


def _types(state: SessionState) -> list[EventType]:
    return [e.type for e in state.events]


def _post_process(party: str = "P9") -> SessionState:
    state = verified(new_state(), party, Phase.POST_PROCESS)
    state.memory.case_log.discussed_case_ids.append("CL-2048")
    return state


def _send(to: str) -> PlannedAction:
    return PlannedAction(kind=ActionKind.SEND_SUMMARY_EMAIL, params={"to": to})


def test_email_is_sent_to_the_outbox_without_identity_numbers(
    snapshot_repo: InMemoryRepository, tmp_path: Path
) -> None:
    executor = _executor(snapshot_repo, tmp_path)
    state, [result] = executor.run([_send("margaret@email.com")], _post_process())
    assert result.ok
    assert {EventType.SUMMARY_DRAFTED, EventType.EMAIL_SENT, EventType.ACTION_EXECUTED} <= set(_types(state))
    [email] = executor.outbox.outbox(state.session_id)
    assert email.to == "margaret@email.com" and "CL-2048" in email.text
    for secret in ("4472", "1985", "6505212836"):
        assert secret not in email.text
    assert list((tmp_path / "outbox").glob("*.json"))


def test_email_to_invalid_address_or_outside_post_process_fails(
    snapshot_repo: InMemoryRepository, tmp_path: Path
) -> None:
    executor = _executor(snapshot_repo, tmp_path)
    state, [bad] = executor.run([_send("not-an-email")], _post_process())
    assert (
        not bad.ok and EventType.ACTION_FAILED in _types(state) and EventType.EMAIL_SENT not in _types(state)
    )
    wrong_phase = verified(new_state(), "P9", Phase.PROCESS_CASE)
    _, [early] = executor.run([_send("margaret@email.com")], wrong_phase)
    assert not early.ok


def test_representative_can_only_send_to_the_address_on_file(
    snapshot_repo: InMemoryRepository, tmp_path: Path
) -> None:
    state = _post_process()
    state.memory.representative.caller_role = CallerRole.AUTHORIZED_REPRESENTATIVE
    executor = _executor(snapshot_repo, tmp_path)
    _, [other] = executor.run([_send("david@example.com")], state)
    assert not other.ok
    _, [own] = executor.run([_send("margaret@email.com")], state)
    assert own.ok


def test_outbox_write_failure_is_reported_not_hidden(
    snapshot_repo: InMemoryRepository, tmp_path: Path
) -> None:
    blocker = tmp_path / "blocked"
    blocker.write_text("a file where a directory should be", encoding="utf-8")
    executor = _executor(snapshot_repo, tmp_path)
    executor.outbox = MockOutbox(blocker)
    state, [result] = executor.run([_send("margaret@email.com")], _post_process())
    assert not result.ok and EventType.EMAIL_SENT not in _types(state)


def test_consent_request_needs_a_matched_authorization(
    snapshot_repo: InMemoryRepository, tmp_path: Path
) -> None:
    executor = _executor(snapshot_repo, tmp_path)
    state = new_state()
    state.memory.identity.party_id = "P9"
    action = PlannedAction(kind=ActionKind.REQUEST_CONSENT, params={"consent_scenario": "default"})
    _, [refused] = executor.run([action], state)
    assert not refused.ok
    state.memory.representative.authorization = AuthorizationStatus.MATCHED
    after, [ok] = executor.run([action], state)
    assert ok.ok and after.memory.representative.consent_status is ConsentStatus.PENDING
    assert after.memory.representative.consent_request_id
    assert _types(after)[:2] == [EventType.CONSENT_REQUESTED, EventType.CONSENT_STATUS]
    [sms] = list((tmp_path / "sms").glob("*.json"))
    text = sms.read_text(encoding="utf-8")
    assert "6505212836" not in text and "650-521" not in text  # the mock SMS keeps only a masked number


def test_transfer_creates_a_ticket_without_party_id_before_verification(
    snapshot_repo: InMemoryRepository, tmp_path: Path
) -> None:
    executor = _executor(snapshot_repo, tmp_path)
    state = new_state(Phase.ESCALATED)
    state.memory.identity.party_id = "P9"  # identity passed for a representative, but not verified
    action = PlannedAction(
        kind=ActionKind.TRANSFER_TO_LIVE_AGENT, params={"reason": EscalationReason.SAFETY.value}
    )
    after, [result] = executor.run([action], state)
    assert result.ok and after.handoff is not None
    assert after.handoff.reason is EscalationReason.SAFETY and after.handoff.party_id is None
    assert EventType.ESCALATED in _types(after)


@pytest.mark.parametrize(
    ("scenario", "statuses"),
    [
        ("default", [("pending", False), ("approved", True), ("approved", True)]),
        (
            "timeout",
            [
                ("pending", False),
                ("pending", False),
                ("pending", False),
                ("pending", False),
                ("pending", True),
            ],
        ),
        ("declined", [("pending", False), ("declined", True)]),
    ],
)
def test_consent_service_consumes_one_status_per_request_or_poll(
    scenario: str, statuses: list[tuple[str, bool]]
) -> None:
    service = ScenarioConsentService(merged_repository())
    request_id, first = service.request("P9", scenario)
    seen = [(first.status, first.exhausted)] + [
        (o.status, o.exhausted) for o in (service.poll(request_id) for _ in statuses[1:])
    ]
    assert seen == statuses
