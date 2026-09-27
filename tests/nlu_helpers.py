"""Builders for LLM wire payloads in tests."""

from typing import Any

from sop_agent.nlu.wire import NLUWireBase


def wire_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        name: "" for name, f in NLUWireBase.model_fields.items() if f.annotation is str
    }
    payload.update(
        dialog_acts=[], id_kind="none", declined_fields=[], caller_role="unknown", claim_status="none",
        date_year=0, date_month=0, date_day=0, description_keywords=[], intents=[], followup_topics=[],
        questions=[], unavailable_documents=[], no_substitutes_available=False, scope="in_scope",
        emotion="neutral", emotion_intensity=0, confirmation="none", requests_live_agent=False,
        manipulation_attempt=False, abusive=False, safety_concern=False,
    )  # fmt: skip
    payload.update(overrides)
    return payload
