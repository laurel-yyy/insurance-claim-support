"""Merge one turn's NLU into memory (SPEC §9.1.2).

Runs in every phase, which is what makes memory cross-phase. Pure: returns a new state plus this turn's
events (also appended to the state's timeline); the input state is never mutated. Nothing here changes the
phase or verification status (INV-1).
"""

from sop_agent.domain.dates import DateHint
from sop_agent.domain.enums import (
    PATHS_NEEDING_CLAIM,
    CallerRole,
    IdentityField,
    IdentityStatus,
    Path,
    Phase,
)
from sop_agent.domain.normalize import normalize_identity_field, normalize_name, normalize_policy_number
from sop_agent.memory.state import (
    EMOTION_HISTORY_LIMIT,
    DeferredQuestion,
    EmotionReading,
    FieldValue,
    IntentCandidate,
    Memory,
    SessionState,
)
from sop_agent.nlu.schema import NLUResult, QuestionKind
from sop_agent.sop.directive import Event, EventType

PATH_HINT_MIN_CONFIDENCE = 0.5

_MONTH_NAMES = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)  # fmt: skip


def merge(state: SessionState, nlu: NLUResult, turn: int) -> tuple[SessionState, list[Event]]:
    """Apply every merge rule and return (new state, events of this merge)."""
    new = state.model_copy(deep=True)
    events = _Events(turn, state.phase)
    _merge_representative(new.memory, nlu)
    _merge_identity(new, nlu, events)
    _merge_case_hints(new.memory, nlu, events)
    _merge_intents(new.memory, nlu, turn)
    _merge_questions(new, nlu, turn, events)
    new.memory.unavailable_documents |= {d.strip().casefold() for d in nlu.unavailable_documents if d.strip()}
    new.memory.emotion_history.append(
        EmotionReading(label=nlu.emotion, intensity=nlu.emotion_intensity, turn=turn)
    )
    new.memory.emotion_history = new.memory.emotion_history[-EMOTION_HISTORY_LIMIT:]
    new.events.extend(events.items)
    return new, events.items


class _Events:
    def __init__(self, turn: int, phase: Phase) -> None:
        self.turn, self.phase, self.items = turn, phase, list[Event]()

    def add(self, kind: EventType, **data: object) -> None:
        self.items.append(Event(type=kind, turn=self.turn, phase=self.phase, data=dict(data)))


def _merge_representative(memory: Memory, nlu: NLUResult) -> None:
    """Non-empty values replace; once a representative, never downgraded within the session (A1)."""
    rep = memory.representative
    if nlu.caller_role is CallerRole.AUTHORIZED_REPRESENTATIVE:
        rep.caller_role = CallerRole.AUTHORIZED_REPRESENTATIVE
    elif nlu.caller_role is CallerRole.POLICYHOLDER and rep.caller_role is CallerRole.UNKNOWN:
        rep.caller_role = CallerRole.POLICYHOLDER
    if nlu.representative_name and nlu.representative_name.strip():
        rep.representative_name = nlu.representative_name.strip()
    if nlu.relationship_to_policyholder and nlu.relationship_to_policyholder.strip():
        rep.relationship = nlu.relationship_to_policyholder.strip()


def _representative_own_name(memory: Memory) -> str | None:
    rep = memory.representative
    if rep.caller_role is not CallerRole.AUTHORIZED_REPRESENTATIVE or not rep.representative_name:
        return None
    return normalize_name(rep.representative_name)


def _merge_identity(state: SessionState, nlu: NLUResult, events: _Events) -> None:
    """Valid values replace old ones (a correction); invalid ones are dropped with an event (V7).

    Identity is frozen once it leaves UNVERIFIED: a verified or locked session never re-verifies.
    """
    identity = state.memory.identity
    if identity.status is not IdentityStatus.UNVERIFIED:
        return
    today = state.session_settings.today
    rep_name = _representative_own_name(state.memory)
    for name, mention in nlu.identity.items():
        value = normalize_identity_field(name, mention.raw, today)
        if value is None:
            events.add(EventType.IDENTITY_FIELD_INVALID, field=name.value)
            continue
        if name is IdentityField.FULL_NAME and value == rep_name:
            continue  # The representative's own name is not an identity field (A2)
        identity.values[name] = FieldValue(value=value, turn=events.turn, source=mention.source)
        identity.declined.discard(name)
        events.add(EventType.IDENTITY_FIELD_CAPTURED, field=name.value)
    stored_name = identity.values.get(IdentityField.FULL_NAME)
    if rep_name is not None and stored_name is not None and stored_name.value == rep_name:
        del identity.values[IdentityField.FULL_NAME]
    if nlu.policy_number and (policy := normalize_policy_number(nlu.policy_number)):
        identity.policy_number = policy
    if nlu.id_kind is not None:
        identity.id_kind_stated = nlu.id_kind.value
    for declined in nlu.declined_fields:
        if declined not in nlu.identity and declined not in identity.declined:
            identity.declined.add(declined)
            events.add(EventType.IDENTITY_FIELD_DECLINED, field=declined.value)


def _merge_case_hints(memory: Memory, nlu: NLUResult, events: _Events) -> None:
    """Non-empty values replace; keywords append without duplicates (§9.1.2)."""
    hints = memory.case_hints
    stored: list[str] = []
    if nlu.case_id and nlu.case_id.strip():
        hints.case_id = nlu.case_id.strip().upper()
        stored.append("case_id")
    if nlu.case_type and nlu.case_type.strip():
        hints.case_type = nlu.case_type.strip().casefold()
        stored.append("case_type")
    if nlu.claim_status is not None:
        hints.status = nlu.claim_status
        stored.append("status")
    if nlu.date_hint is not None and not nlu.date_hint.is_empty():
        hints.date_hint = nlu.date_hint
        stored.append("date")
    new_keywords = [k.strip().casefold() for k in nlu.description_keywords if k.strip()]
    added = [k for k in dict.fromkeys(new_keywords) if k not in hints.keywords]
    if added:
        hints.keywords.extend(added)
        stored.append("keywords")
    if not stored:
        return
    mention = describe_hints(nlu)
    if mention and mention not in hints.raw_mentions:
        hints.raw_mentions.append(mention)
    events.add(EventType.HINT_STORED, kinds=stored)


def describe_hints(nlu: NLUResult) -> str:
    """Code-built phrase for acknowledging hints, e.g. "denied healthcare claim, January 2026".

    Built only from the caller's own stated hints, never from record data or free LLM text.
    """
    words = [w for w in (nlu.claim_status, nlu.case_type) if w]
    parts = [" ".join([*(str(w).strip().casefold() for w in words), "claim"])] if words else []
    if nlu.case_id and nlu.case_id.strip():
        parts.append(nlu.case_id.strip().upper())
    if nlu.date_hint is not None and (when := _describe_date(nlu.date_hint)):
        parts.append(when)
    if nlu.description_keywords:
        parts.append(", ".join(k.strip().casefold() for k in nlu.description_keywords if k.strip()))
    return ", ".join(p for p in parts if p)


def _describe_date(hint: DateHint) -> str:
    month = _MONTH_NAMES[hint.month - 1] if hint.month and 1 <= hint.month <= 12 else ""
    day = str(hint.day) if hint.day and month else ""
    year = str(hint.year) if hint.year else ""
    return " ".join(p for p in (month, day, year) if p)


def _merge_intents(memory: Memory, nlu: NLUResult, turn: int) -> None:
    """Merge by path, keep the highest confidence, record the latest mention."""
    for intent in nlu.intents:
        confidence = min(max(intent.confidence, 0.0), 1.0)
        current = memory.intent_candidates.get(intent.path)
        best = max(confidence, current.confidence) if current else confidence
        memory.intent_candidates[intent.path] = IntentCandidate(
            path=intent.path, confidence=best, last_turn=turn
        )


def _merge_questions(state: SessionState, nlu: NLUResult, turn: int, events: _Events) -> None:
    """Defer account questions until they can be answered (§9.1.2, §8.2.7).

    Only ACCOUNT questions (or ones with a missing label) are deferred. GENERAL, PROCESS and OUT_OF_SCOPE
    questions are left for the policy to answer or decline this turn, in any phase.
    """
    if not _account_questions_must_wait(state):
        return
    path = _likely_claim_path(nlu)
    known = {q.text.casefold() for q in state.memory.deferred_questions}
    for question in nlu.questions:
        text = question.text.strip()
        if question.kind not in (QuestionKind.ACCOUNT, None) or not text or text.casefold() in known:
            continue
        state.memory.deferred_questions.append(DeferredQuestion(text=text, turn=turn, path=path))
        known.add(text.casefold())
        events.add(EventType.QUESTION_DEFERRED, path=path.value if path else None)


def _account_questions_must_wait(state: SessionState) -> bool:
    """Record data needs completed verification, and claim answers need a selected claim."""
    if state.memory.identity.status is not IdentityStatus.VERIFIED:
        return True
    return state.phase is Phase.RESOLVE_INTENT and state.memory.selected_case_id is None


def _likely_claim_path(nlu: NLUResult) -> Path | None:
    """Best claim-related intent in the same message, used later to route the deferred question."""
    claim_intents = [
        i for i in nlu.intents if i.path in PATHS_NEEDING_CLAIM and i.confidence >= PATH_HINT_MIN_CONFIDENCE
    ]
    return max(claim_intents, key=lambda i: i.confidence).path if claim_intents else None


def mark_deferred_answered(state: SessionState) -> SessionState:
    """Mark deferred questions answered once a reply that answered them was delivered (§8.2.7, D30).

    Called by the orchestrator after a successful PROCESS_CASE reply; a fallback reply doesn't answer them.
    """
    new = state.model_copy(deep=True)
    for question in new.memory.deferred_questions:
        question.answered = True
    return new
