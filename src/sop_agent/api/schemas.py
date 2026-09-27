"""Request/response DTOs for the HTTP API (SPEC §13.1). No response ever contains an API key."""

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, Field, SecretStr

from sop_agent.data.loaders import DataSummary

MAX_MESSAGE_CHARS = 2000


class HealthResponse(BaseModel):
    """Reports whether a key is configured, never the key itself (INV-9)."""

    status: str
    llm_configured: bool
    allow_client_api_key: bool
    demo_today: date
    company_name: str
    agent_name: str
    data_summary: DataSummary


class CreateSessionRequest(BaseModel):
    api_key: SecretStr | None = None
    consent_scenario: str | None = None


class MessageRequest(BaseModel):
    text: str = Field(min_length=1, max_length=MAX_MESSAGE_CHARS)


class DebugView(BaseModel):
    """Everything the Inspector shows, fully masked (§13.1)."""

    phase: str
    verify_stage: str | None
    identity_checklist: dict[str, str]
    verification: dict[str, Any]
    representative: dict[str, Any]
    memory: dict[str, Any]
    counters: dict[str, int]
    pending_question: dict[str, Any] | None
    events_this_turn: list[dict[str, Any]]
    timeline: list[dict[str, Any]]
    last_turn: dict[str, Any] | None
    email_draft: dict[str, Any] | None
    handoff: dict[str, Any] | None


class TurnResponse(BaseModel):
    session_id: str
    reply: str
    phase: str
    verify_stage: str | None
    phase_trail: list[str]
    quick_replies: list[str]
    ended: bool
    escalated: bool
    debug: DebugView


class EmailRecordOut(BaseModel):
    message_id: str
    to: str  # Masked
    subject: str
    text: str
    html: str
    sent_at: datetime


class ScenarioInfo(BaseModel):
    name: str
    description: str
    consent_scenario: str
    turns: list[str]
