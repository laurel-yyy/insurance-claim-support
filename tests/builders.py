"""Builders for session state and NLU results in unit tests (no LLM involved)."""

from datetime import UTC, date, datetime
from typing import Any

from sop_agent.domain.enums import IdentityField, Phase
from sop_agent.memory.state import FieldValue, IdentityState, SessionSettings, SessionState
from sop_agent.nlu.schema import FieldMention, NLUResult, ValueSource
from sop_agent.sop.verification import VerifyConfig

TODAY = date(2026, 3, 10)
CFG = VerifyConfig(min_matches=3, max_failed_attempts=3)


def new_state(phase: Phase = Phase.VERIFY_ID, **overrides: Any) -> SessionState:
    state = SessionState(
        session_id="test-session",
        session_settings=SessionSettings(today=TODAY),
        created_at=datetime(2026, 3, 10, 9, 0, tzinfo=UTC),
        **overrides,
    )
    state.phase = phase
    return state


def identity(policy_number: str | None = None, **values: str) -> IdentityState:
    """IdentityState with already-normalized values, keyed by IdentityField value names."""
    return IdentityState(
        values={
            IdentityField(name): FieldValue(value=value, turn=1, source=ValueSource.LLM)
            for name, value in values.items()
        },
        policy_number=policy_number,
    )


def nlu(identity_raw: dict[IdentityField, str] | None = None, **fields: Any) -> NLUResult:
    mentions = {
        name: FieldMention(raw=raw, source=ValueSource.LLM) for name, raw in (identity_raw or {}).items()
    }
    return NLUResult(identity=mentions, **fields)
