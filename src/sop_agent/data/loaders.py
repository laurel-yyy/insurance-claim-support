"""Load and validate the fixtures directory into an InMemoryRepository (SPEC §6.5).

Parsing is tolerant where a swapped dataset could legitimately differ (optional fields, unknown statuses or
case types, duplicate aliases) and fails fast on structural errors, naming the file and the record.
"""

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from sop_agent.data.repository import InMemoryRepository
from sop_agent.domain.enums import ClaimStatus
from sop_agent.domain.errors import AmountParseError, DataLoadError
from sop_agent.domain.models import (
    AuthorizedRepresentative,
    Claim,
    ConsentScenario,
    DocumentGuideline,
    FieldInfo,
    FollowupTopic,
    Policyholder,
)
from sop_agent.domain.money import parse_amount

POLICYHOLDERS_FILE = "policyholders.json"
CLAIMS_FILE = "claims.json"
REPRESENTATIVES_FILE = "representatives.json"
CONSENT_FILE = "consent_scenarios.json"
SCHEMA_FILE = "claim_schema.json"
GUIDELINE_FILE = "required_document_guideline.json"
FALLBACK_LANGUAGE = "en"
AMOUNT_FIELDS = ("expected_reimbursement_amount", "allowed_max_amount", "net_pay", "net_fee")


class DataSummary(BaseModel):
    """Counts reported by /api/health and the startup log line."""

    model_config = ConfigDict(frozen=True)

    policyholders: int
    claims: int
    representatives: int
    consent_scenarios: tuple[str, ...]
    guidance_topics: int


@dataclass(frozen=True)
class RawFixtures:
    """Decoded JSON of each fixture file, before validation. Lets tests merge extra records in."""

    policyholders: list[Any] = field(default_factory=list)
    claims: list[Any] = field(default_factory=list)
    representatives: list[Any] = field(default_factory=list)
    consent_scenarios: dict[str, Any] = field(default_factory=dict)
    claim_schema: dict[str, Any] = field(default_factory=dict)
    guideline: dict[str, Any] = field(default_factory=dict)


def read_json(path: Path) -> Any:
    """Read one fixture file; a missing or malformed file is a startup error."""
    if not path.is_file():
        raise DataLoadError(path.name, "-", "required file is missing")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise DataLoadError(path.name, "-", f"invalid JSON: {exc}") from exc


def read_raw_fixtures(fixtures_dir: Path) -> RawFixtures:
    """Read every required file from the directory without validating record contents."""
    return RawFixtures(
        policyholders=_expect(read_json(fixtures_dir / POLICYHOLDERS_FILE), list, POLICYHOLDERS_FILE),
        claims=_expect(read_json(fixtures_dir / CLAIMS_FILE), list, CLAIMS_FILE),
        representatives=_expect(read_json(fixtures_dir / REPRESENTATIVES_FILE), list, REPRESENTATIVES_FILE),
        consent_scenarios=_expect(read_json(fixtures_dir / CONSENT_FILE), dict, CONSENT_FILE),
        claim_schema=_expect(read_json(fixtures_dir / SCHEMA_FILE), dict, SCHEMA_FILE),
        guideline=_expect(read_json(fixtures_dir / GUIDELINE_FILE), dict, GUIDELINE_FILE),
    )


def load_repository(fixtures_dir: Path, language: str = FALLBACK_LANGUAGE) -> InMemoryRepository:
    """Read and validate the fixtures directory."""
    return build_repository(read_raw_fixtures(fixtures_dir), language)


def build_repository(raw: RawFixtures, language: str = FALLBACK_LANGUAGE) -> InMemoryRepository:
    """Validate decoded fixtures, check cross-file references, and index them."""
    policyholders = _parse_list(raw.policyholders, POLICYHOLDERS_FILE, "party_id", _parse_policyholder)
    party_ids = _unique_ids(policyholders, POLICYHOLDERS_FILE, lambda p: p.party_id)
    claims = _parse_list(raw.claims, CLAIMS_FILE, "case_id", _parse_claim)
    _unique_ids(claims, CLAIMS_FILE, lambda c: c.case_id)
    for claim in claims:
        if claim.party_id not in party_ids:
            raise DataLoadError(CLAIMS_FILE, claim.case_id, f"unknown party_id {claim.party_id!r}")
    representatives = _parse_list(
        raw.representatives, REPRESENTATIVES_FILE, "rep_name", _parse_representative
    )
    for rep in representatives:
        if rep.buyer_party_id not in party_ids:
            raise DataLoadError(
                REPRESENTATIVES_FILE, rep.rep_name, f"unknown party_id {rep.buyer_party_id!r}"
            )
    return InMemoryRepository(
        policyholders=policyholders,
        claims=claims,
        representatives=representatives,
        consent_scenarios=_parse_consent(raw.consent_scenarios),
        glossary=_parse_glossary(raw.claim_schema),
        guideline=_parse_guideline(raw.guideline, language),
    )


def summarize(repo: InMemoryRepository) -> DataSummary:
    """Summarize what was loaded, for the health check and the startup log."""
    return DataSummary(
        policyholders=len(repo.policyholders()),
        claims=len(repo.claims()),
        representatives=len(repo.representatives()),
        consent_scenarios=tuple(repo.consent_scenario_names()),
        guidance_topics=len(repo.document_guideline().followup_topics),
    )


# --- record parsers -------------------------------------------------------------------------------------


def _parse_policyholder(rec: Mapping[str, Any], file: str, key: str) -> Policyholder:
    phone = _req_str(rec, "phone", file, key)
    email = _req_str(rec, "email", file, key)
    name = _req_str(rec, "name", file, key)
    return Policyholder(
        party_id=_req_str(rec, "party_id", file, key),
        name=name,
        name_aliases=_aliases(rec, "name_aliases", name, file, key),
        policy_number=_req_str(rec, "policy_number", file, key),
        dob=_req_date(rec, "dob", file, key),
        id_type=_req_str(rec, "id_type", file, key),
        id_last4=_req_str(rec, "id_last4", file, key),
        phone=phone,
        phone_aliases=_aliases(rec, "phone_aliases", phone, file, key),
        email=email,
        email_aliases=_aliases(rec, "email_aliases", email, file, key),
    )


def _parse_claim(rec: Mapping[str, Any], file: str, key: str) -> Claim:
    raw_status = _req_str(rec, "status", file, key)
    amounts = {name: _opt_amount(rec, name, file, key) for name in AMOUNT_FIELDS}
    deadline = rec.get("appeal_deadline")
    return Claim(
        case_id=_req_str(rec, "case_id", file, key),
        party_id=_req_str(rec, "party_id", file, key),
        case_type=_req_str(rec, "case_type", file, key).strip().casefold(),
        created_at=_req_date(rec, "created_at", file, key),
        status=ClaimStatus.from_raw(raw_status),
        raw_status=raw_status,
        summary=_req_str(rec, "summary", file, key),
        denial_reason=_opt_str(rec, "denial_reason", file, key),
        documents_needed=_str_tuple(rec.get("documents_needed"), "documents_needed", file, key),
        appeal_deadline=None if deadline in (None, "") else _req_date(rec, "appeal_deadline", file, key),
        **amounts,
    )


def _parse_representative(rec: Mapping[str, Any], file: str, key: str) -> AuthorizedRepresentative:
    return AuthorizedRepresentative(
        rep_name=_req_str(rec, "rep_name", file, key),
        relationship=_req_str(rec, "relationship", file, key),
        buyer_name=_req_str(rec, "buyer_name", file, key),
        buyer_party_id=_req_str(rec, "buyer_party_id", file, key),
    )


def _parse_consent(raw: Mapping[str, Any]) -> list[ConsentScenario]:
    scenarios = []
    for name, body in raw.items():
        if not isinstance(body, Mapping):
            raise DataLoadError(CONSENT_FILE, name, "scenario must be an object")
        sequence = _str_tuple(body.get("status_sequence"), "status_sequence", CONSENT_FILE, name)
        if not sequence:
            raise DataLoadError(CONSENT_FILE, name, "status_sequence must be a non-empty list")
        scenarios.append(ConsentScenario(name=name, status_sequence=tuple(s.casefold() for s in sequence)))
    return scenarios


def _parse_glossary(raw: Mapping[str, Any]) -> dict[str, FieldInfo]:
    fields = raw.get("field_descriptions", {})
    if not isinstance(fields, Mapping):
        raise DataLoadError(SCHEMA_FILE, "field_descriptions", "must be an object")
    glossary = {}
    for name, body in fields.items():
        if not isinstance(body, Mapping):
            raise DataLoadError(SCHEMA_FILE, name, "field description must be an object")
        glossary[name] = FieldInfo(
            name=name,
            description=_req_str(body, "description", SCHEMA_FILE, name),
            example=_opt_str(body, "example", SCHEMA_FILE, name),
        )
    return glossary


def _parse_guideline(raw: Mapping[str, Any], language: str) -> DocumentGuideline:
    def text(node: Any, record: str) -> str:
        return _localized(node, language, record)

    def text_map(section: str) -> dict[str, str]:
        node = raw.get(section, {})
        if not isinstance(node, Mapping):
            raise DataLoadError(GUIDELINE_FILE, section, "must be an object")
        return {k.strip().casefold(): text(v, f"{section}.{k}") for k, v in node.items()}

    topics_raw = raw.get("claim_followup_guidance", [])
    if not isinstance(topics_raw, list):
        raise DataLoadError(GUIDELINE_FILE, "claim_followup_guidance", "must be a list")
    return DocumentGuideline(
        default_guidance=text(raw.get("default_guidance"), "default_guidance"),
        case_type_guidance=text_map("case_type_guidance"),
        document_guidance=text_map("document_guidance"),
        document_alternative_guidance=text_map("document_alternative_guidance"),
        settings=text_map("claim_followup_settings"),
        followup_topics=tuple(_parse_topic(t, i, language) for i, t in enumerate(topics_raw)),
        followup_fallback=text(raw.get("claim_followup_fallback"), "claim_followup_fallback"),
    )


def _parse_topic(rec: Any, index: int, language: str) -> FollowupTopic:
    key = f"claim_followup_guidance[{index}]"
    if not isinstance(rec, Mapping):
        raise DataLoadError(GUIDELINE_FILE, key, "topic must be an object")
    topic = _req_str(rec, "topic", GUIDELINE_FILE, key)
    return FollowupTopic(
        topic=topic,
        intent_hints=tuple(
            h.casefold() for h in _str_tuple(rec.get("intent_hints"), "intent_hints", GUIDELINE_FILE, topic)
        ),
        requires_documents=bool(rec.get("requires_documents", False)),
        match_any=tuple(
            dict.fromkeys(
                m.casefold() for m in _str_tuple(rec.get("match_any"), "match_any", GUIDELINE_FILE, topic)
            )
        ),
        template=_localized(rec, language, topic),
    )


# --- field helpers --------------------------------------------------------------------------------------


def _expect[T](value: Any, kind: type[T], file: str) -> T:
    if not isinstance(value, kind):
        raise DataLoadError(file, "-", f"top level must be a JSON {kind.__name__}")
    return value


def _parse_list[T](
    records: Sequence[Any], file: str, id_key: str, parse: Callable[[Mapping[str, Any], str, str], T]
) -> list[T]:
    parsed = []
    for index, rec in enumerate(records):
        if not isinstance(rec, Mapping):
            raise DataLoadError(file, f"#{index}", "record must be an object")
        key = str(rec.get(id_key) or f"#{index}")
        parsed.append(parse(rec, file, key))
    return parsed


def _unique_ids[T](items: Sequence[T], file: str, get_id: Callable[[T], str]) -> set[str]:
    seen: set[str] = set()
    for item in items:
        item_id = get_id(item)
        if item_id in seen:
            raise DataLoadError(file, item_id, "duplicate id")
        seen.add(item_id)
    return seen


def _req_str(rec: Mapping[str, Any], name: str, file: str, key: str) -> str:
    value = rec.get(name)
    if not isinstance(value, str) or not value.strip():
        raise DataLoadError(file, key, f"missing or empty required field {name!r}")
    return value.strip()


def _opt_str(rec: Mapping[str, Any], name: str, file: str, key: str) -> str | None:
    value = rec.get(name)
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise DataLoadError(file, key, f"field {name!r} must be a string")
    return value.strip()


def _req_date(rec: Mapping[str, Any], name: str, file: str, key: str) -> date:
    raw = _req_str(rec, name, file, key)
    try:
        return date.fromisoformat(raw)
    except ValueError as exc:
        raise DataLoadError(file, key, f"field {name!r} is not an ISO date: {raw!r}") from exc


def _opt_amount(rec: Mapping[str, Any], name: str, file: str, key: str) -> Decimal | None:
    value = rec.get(name)
    if value is None or value == "":
        return None
    try:
        return parse_amount(str(value))
    except AmountParseError as exc:
        raise DataLoadError(file, key, f"field {name!r} is not an amount: {value!r}") from exc


def _str_tuple(value: Any, name: str, file: str, key: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise DataLoadError(file, key, f"field {name!r} must be a list of strings")
    return tuple(v.strip() for v in value if v.strip())


def _aliases(rec: Mapping[str, Any], name: str, primary: str, file: str, key: str) -> tuple[str, ...]:
    """Deduplicate aliases and drop any that repeat the primary value."""
    values = _str_tuple(rec.get(name), name, file, key)
    return tuple(v for v in dict.fromkeys(values) if v.casefold() != primary.casefold())


def _localized(node: Any, language: str, record: str) -> str:
    """Pick the configured language, falling back to English (§6.2.12)."""
    if isinstance(node, Mapping):
        for lang in (language, FALLBACK_LANGUAGE):
            value = node.get(lang)
            if isinstance(value, str) and value.strip():
                return value.strip()
    raise DataLoadError(GUIDELINE_FILE, record, f"missing text for language {language!r} and fallback 'en'")
