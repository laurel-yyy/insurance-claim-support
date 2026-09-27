"""Frozen domain models built from the fixture files."""

from collections.abc import Mapping
from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from sop_agent.domain.enums import ClaimStatus


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Policyholder(_Frozen):
    """Account holder. Aliases exist because the data comes from an audio demo."""

    party_id: str
    name: str
    name_aliases: tuple[str, ...] = ()
    policy_number: str
    dob: date
    id_type: str
    id_last4: str
    phone: str
    phone_aliases: tuple[str, ...] = ()
    email: str
    email_aliases: tuple[str, ...] = ()


class Claim(_Frozen):
    """A claim. `raw_status` keeps the original value when `status` falls back to OTHER."""

    case_id: str
    party_id: str
    case_type: str
    created_at: date
    status: ClaimStatus
    raw_status: str
    summary: str
    denial_reason: str | None = None
    documents_needed: tuple[str, ...] = ()
    appeal_deadline: date | None = None
    expected_reimbursement_amount: Decimal | None = None
    allowed_max_amount: Decimal | None = None
    net_pay: Decimal | None = None
    net_fee: Decimal | None = None


class AuthorizedRepresentative(_Frozen):
    """A third party allowed to call for a policyholder. Not to be confused with a live agent."""

    rep_name: str
    relationship: str
    buyer_name: str
    buyer_party_id: str


class ConsentScenario(_Frozen):
    """Simulated sequence of real-time consent statuses (A6)."""

    name: str
    status_sequence: tuple[str, ...]


class FieldInfo(_Frozen):
    """Meaning of an amount field, used as a glossary when explaining amounts."""

    name: str
    description: str
    example: str | None = None


class FollowupTopic(_Frozen):
    """Follow-up answer template; `template` is already resolved to the configured language."""

    topic: str
    intent_hints: tuple[str, ...]
    requires_documents: bool
    match_any: tuple[str, ...] = ()
    template: str


class DocumentGuideline(_Frozen):
    """Language-resolved document guidance: the main knowledge source for PROCESS_CASE."""

    default_guidance: str
    case_type_guidance: Mapping[str, str]
    document_guidance: Mapping[str, str]
    document_alternative_guidance: Mapping[str, str]
    settings: Mapping[str, str]
    followup_topics: tuple[FollowupTopic, ...]
    followup_fallback: str
