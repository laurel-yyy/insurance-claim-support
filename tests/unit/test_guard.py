"""OutputGuard rules G1-G5 (INV-3)."""

import pytest

from sop_agent.agent.guard import GuardContext, OutputGuard, Rule
from sop_agent.agent.sensitive_index import SensitiveIndex
from sop_agent.data.repository import InMemoryRepository
from sop_agent.domain.enums import IdentityField, Phase
from sop_agent.sop.directive import EventType


@pytest.fixture
def guard(snapshot_repo: InMemoryRepository) -> OutputGuard:
    return OutputGuard(SensitiveIndex(snapshot_repo))


def _ctx(
    *,
    verified: bool = False,
    party_id: str | None = None,
    phase: Phase = Phase.VERIFY_ID,
    caller: tuple[str, ...] = (),
    identity: dict[IdentityField, str] | None = None,
    grounding: str = "",
    tools: str = "",
    events: frozenset[EventType] = frozenset(),
) -> GuardContext:
    return GuardContext(
        verified=verified,
        party_id=party_id,
        phase=phase,
        caller_texts=list(caller),
        caller_identity=identity or {},
        grounding_text=grounding,
        tool_text=tools,
        event_types=events,
    )


def _rules(guard: OutputGuard, reply: str, ctx: GuardContext) -> set[Rule]:
    return {v.rule for v in guard.check(reply, ctx)}


@pytest.mark.parametrize(
    "leak",
    [
        "Your claim CL-2048 was denied.",
        "The allowed amount was $1,450.00.",
        "It was created on January 12, 2026.",
        "We have margaret@email.com on file.",
        "Policy POL-9921 is active.",
        "The review file did not include the pathology report and the treating provider office note.",
    ],
)
def test_g1_blocks_record_data_before_verification(guard: OutputGuard, leak: str) -> None:
    assert Rule.G1 in _rules(guard, leak, _ctx())


def test_g1_allows_tokens_the_caller_said(guard: OutputGuard) -> None:
    ctx = _ctx(caller=("I'm Margaret Chen, policy POL-9921, about claim CL-2048",))
    assert _rules(guard, "Thanks Margaret Chen, I've noted policy POL-9921 and claim CL-2048.", ctx) == set()


def test_g1_yearless_date_is_allowed_only_if_the_caller_said_that_day(guard: OutputGuard) -> None:
    said = _ctx(caller=("the claim from January 12, 2026",))
    assert Rule.G1 not in _rules(guard, "You mentioned January 12.", said)
    assert Rule.G1 in _rules(guard, "Was it January 12?", _ctx())


def test_g1_restating_caller_hints_without_record_tokens_is_fine(guard: OutputGuard) -> None:
    assert (
        _rules(guard, "I've noted you're calling about a denied healthcare claim from January.", _ctx())
        == set()
    )


def test_g2_blocks_another_policyholders_data_after_verification(guard: OutputGuard) -> None:
    ctx = _ctx(verified=True, party_id="P9", phase=Phase.PROCESS_CASE, grounding="CL-3001")
    assert Rule.G2 in _rules(guard, "Claim CL-3001 needs a diagnosis report.", ctx)


def test_g2_allows_own_data(guard: OutputGuard) -> None:
    ctx = _ctx(verified=True, party_id="P9", phase=Phase.PROCESS_CASE, grounding='{"allowed": "$1,450.00"}')
    assert _rules(guard, "Claim CL-2048 allowed up to $1,450.00.", ctx) == set()


@pytest.mark.parametrize(
    ("reply", "field"),
    [
        ("Your date of birth is March 15, 1985.", IdentityField.DOB),
        ("The last four digits are 4472.", IdentityField.ID_LAST4),
        ("I have your number as (650) 521-2836.", IdentityField.PHONE),
    ],
)
def test_g3_blocks_pii_echo_even_after_verification(
    guard: OutputGuard, reply: str, field: IdentityField
) -> None:
    identity = {
        IdentityField.DOB: "1985-03-15",
        IdentityField.ID_LAST4: "4472",
        IdentityField.PHONE: "6505212836",
    }
    for verified in (False, True):
        ctx = _ctx(verified=verified, party_id="P9" if verified else None, caller=("x",), identity=identity)
        violations = guard.check(reply, ctx)
        assert (Rule.G3, field.value) in {(v.rule, v.kind) for v in violations}


def test_g4_flags_ungrounded_amounts_and_dates(guard: OutputGuard) -> None:
    ctx = _ctx(
        verified=True,
        party_id="P9",
        phase=Phase.PROCESS_CASE,
        grounding='{"x": "$1,450.00", "d": "2026-03-18"}',
    )
    assert _rules(guard, "You'll get $500.00 by April 1, 2026.", ctx) == {Rule.G4}
    assert _rules(guard, "The allowed amount is $1,450 and the deadline is March 18, 2026.", ctx) == set()


def test_g4_accepts_values_from_tool_results(guard: OutputGuard) -> None:
    ctx = _ctx(verified=True, party_id="P9", phase=Phase.PROCESS_CASE, tools='[{"net_pay": "$780.00"}]')
    assert Rule.G4 not in _rules(guard, "It paid $780.00.", ctx)


def test_g4_only_applies_in_grounded_phases(guard: OutputGuard) -> None:
    ctx = _ctx(verified=True, party_id="P9", phase=Phase.RESOLVE_INTENT)
    assert Rule.G4 not in _rules(guard, "Something about $12.34.", ctx)


@pytest.mark.parametrize(
    ("reply", "event"),
    [
        ("I've sent the summary to your email.", EventType.EMAIL_SENT),
        ("I\N{RIGHT SINGLE QUOTATION MARK}ve texted the policyholder.", EventType.CONSENT_REQUESTED),
        ("I've transferred you to a specialist.", EventType.ESCALATED),
    ],
)
def test_g5_blocks_action_claims_without_the_event(guard: OutputGuard, reply: str, event: EventType) -> None:
    assert Rule.G5 in _rules(guard, reply, _ctx())
    assert Rule.G5 not in _rules(guard, reply, _ctx(events=frozenset({event})))


def test_g5_allows_future_or_ongoing_wording(guard: OutputGuard) -> None:
    assert _rules(guard, "I'll send it once you confirm. I'm connecting you now.", _ctx()) == set()


def test_violations_never_contain_the_leaked_text(guard: OutputGuard) -> None:
    violations = guard.check("Claim CL-2048 for margaret@email.com", _ctx())
    assert violations
    rendered = repr(violations)
    assert "CL-2048" not in rendered and "margaret" not in rendered
