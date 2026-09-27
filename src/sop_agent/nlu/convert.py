"""Wire -> domain conversion and the regex/LLM merge.

Empty wire values become None. For format-type fields, code-normalized regex values win over the LLM; a
disagreement is recorded in `NLUResult.conflicts` so the policy can emit NLU_CONFLICT.
"""

from datetime import date

from sop_agent.domain.dates import DateHint
from sop_agent.domain.enums import ClaimStatus, IdentityField, Path
from sop_agent.domain.normalize import normalize_identity_field, normalize_policy_number
from sop_agent.nlu.patterns import PatternResult
from sop_agent.nlu.schema import (
    Confirmation,
    DialogAct,
    FieldMention,
    IdKind,
    IntentScore,
    NLUResult,
    Question,
    Scope,
    ValueSource,
)
from sop_agent.nlu.wire import NONE, NLUWireBase

FORMAT_FIELDS = (IdentityField.DOB, IdentityField.PHONE, IdentityField.EMAIL, IdentityField.ID_LAST4)
MAX_INTENSITY = 3
POLICY_FIELD = "policy_number"
CASE_FIELD = "case_id"


def _text(value: str) -> str | None:
    value = value.strip()
    return value or None


def _identity(
    wire: NLUWireBase, patterns: PatternResult, today: date
) -> tuple[dict[IdentityField, FieldMention], list[str]]:
    mentions: dict[IdentityField, FieldMention] = {}
    conflicts: list[str] = []
    if name := _text(wire.full_name):
        mentions[IdentityField.FULL_NAME] = FieldMention(raw=name, source=ValueSource.LLM)
    for field in FORMAT_FIELDS:
        raw = _text(getattr(wire, field.value))
        llm = normalize_identity_field(field, raw, today) if raw else None
        regex = patterns.identity.get(field)
        if regex is not None:
            if raw is not None and llm != regex:
                conflicts.append(field.value)
            source = ValueSource.BOTH if llm == regex else ValueSource.REGEX
            mentions[field] = FieldMention(raw=regex, source=source)
        elif raw is not None:  # Invalid LLM values stay raw so memory merge reports IDENTITY_FIELD_INVALID
            mentions[field] = FieldMention(raw=llm or raw, source=ValueSource.LLM)
    return mentions, conflicts


def _id_value(llm_raw: str, regex: str | None, name: str, conflicts: list[str]) -> str | None:
    llm = _text(llm_raw)
    if regex is not None:
        if llm is not None and normalize_policy_number(llm) != normalize_policy_number(regex):
            conflicts.append(name)
        return regex
    return llm


def _date_hint(wire: NLUWireBase) -> DateHint | None:
    hint = DateHint(
        year=wire.date_year if 1900 <= wire.date_year <= 2100 else None,
        month=wire.date_month if 1 <= wire.date_month <= 12 else None,
        day=wire.date_day if 1 <= wire.date_day <= 31 else None,
    )
    return None if hint.is_empty() else hint


def to_domain(wire: NLUWireBase, patterns: PatternResult, today: date, text: str) -> NLUResult:
    identity, conflicts = _identity(wire, patterns, today)
    policy = _id_value(wire.policy_number, patterns.policy_number, POLICY_FIELD, conflicts)
    case_id = _id_value(wire.case_id, patterns.case_id, CASE_FIELD, conflicts)
    return NLUResult(
        text=text,
        dialog_acts=list(wire.dialog_acts),
        identity=identity,
        id_kind=None if wire.id_kind.value == NONE else IdKind(wire.id_kind.value),
        policy_number=policy,
        declined_fields=list(dict.fromkeys(wire.declined_fields)),
        caller_role=wire.caller_role,
        representative_name=_text(wire.representative_name),
        relationship_to_policyholder=_text(wire.relationship_to_policyholder),
        case_id=case_id,
        case_type=_text(wire.case_type),
        claim_status=None if wire.claim_status.value == NONE else ClaimStatus(wire.claim_status.value),
        date_hint=_date_hint(wire),
        description_keywords=[k for k in (_text(k) for k in wire.description_keywords) if k],
        intents=[
            IntentScore(path=Path(i.path.value), confidence=min(max(i.confidence, 0.0), 1.0))
            for i in wire.intents
            if i.path.value != NONE
        ],
        followup_topics=wire.topics(),
        questions=[Question(text=q.text.strip(), kind=q.kind) for q in wire.questions if q.text.strip()],
        unavailable_documents=[d for d in (_text(d) for d in wire.unavailable_documents) if d],
        no_substitutes_available=wire.no_substitutes_available,
        scope=wire.scope,
        off_topic_subject=_text(wire.off_topic_subject),
        emotion=wire.emotion,
        emotion_intensity=min(max(wire.emotion_intensity, 0), MAX_INTENSITY),
        confirmation=None if wire.confirmation.value == NONE else Confirmation(wire.confirmation.value),
        summary_email=_text(wire.summary_email),
        requests_live_agent=wire.requests_live_agent,
        manipulation_attempt=wire.manipulation_attempt,
        abusive=wire.abusive,
        safety_concern=wire.safety_concern,
        conflicts=conflicts,
    )


def degraded_result(patterns: PatternResult, text: str) -> NLUResult:
    """Degraded mode: regex results only; everything else neutral."""
    return NLUResult(
        text=text,
        dialog_acts=[DialogAct.OTHER],
        identity={f: FieldMention(raw=v, source=ValueSource.REGEX) for f, v in patterns.identity.items()},
        policy_number=patterns.policy_number,
        case_id=patterns.case_id,
        scope=Scope.IN_SCOPE,
        degraded=True,
    )
