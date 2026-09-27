"""Identity verification: matching, evaluation, lockout and field suggestions.

Pure functions over IdentityState and the read-only repository. Nothing here tells the caller which field
failed or whether a policy exists (V3, V5, V9); `Verified.matched_fields` is for the audit trace only.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field

from sop_agent.data.repository import ClaimsRepository
from sop_agent.domain.enums import CallerRole, IdentityField, IdentityStatus, Phase, VerifiedAs
from sop_agent.domain.models import Policyholder
from sop_agent.domain.normalize import (
    normalize_email,
    normalize_id_last4,
    normalize_name,
    normalize_phone,
)
from sop_agent.memory.state import IdentityState
from sop_agent.sop.directive import Event, EventType

SUGGESTION_ORDER: tuple[IdentityField, ...] = (
    IdentityField.DOB,
    IdentityField.PHONE,
    IdentityField.EMAIL,
    IdentityField.ID_LAST4,
    IdentityField.FULL_NAME,
)


@dataclass(frozen=True)
class VerifyConfig:
    min_matches: int = 3
    max_failed_attempts: int = 3


@dataclass(frozen=True)
class NeedMore:
    missing: int


@dataclass(frozen=True)
class Verified:
    party_id: str
    matched_fields: frozenset[IdentityField]


@dataclass(frozen=True)
class Ambiguous:
    """More than one record reached the threshold (V6): neither a pass nor a failure."""


@dataclass(frozen=True)
class Failed:
    """No single record reached the threshold (V3)."""


VerificationOutcome = NeedMore | Verified | Ambiguous | Failed


@dataclass(frozen=True)
class IdentityCheck:
    """Result of one pipeline pass over identity: the new state, what was decided, and events."""

    identity: IdentityState
    outcome: VerificationOutcome | None
    events: list[Event] = field(default_factory=list)


def record_values(holder: Policyholder, name: IdentityField) -> set[str]:
    """Normalized primary value plus aliases for one field of a record."""
    match name:
        case IdentityField.FULL_NAME:
            raw, norm = (holder.name, *holder.name_aliases), normalize_name
        case IdentityField.PHONE:
            raw, norm = (holder.phone, *holder.phone_aliases), normalize_phone
        case IdentityField.EMAIL:
            raw, norm = (holder.email, *holder.email_aliases), normalize_email
        case IdentityField.ID_LAST4:
            raw, norm = (holder.id_last4,), normalize_id_last4
        case IdentityField.DOB:
            return {holder.dob.isoformat()}
    return {value for r in raw if (value := norm(r)) is not None}


def matched_fields(holder: Policyholder, provided: dict[IdentityField, str]) -> frozenset[IdentityField]:
    """Fields whose provided value equals the record's primary value or an alias. No fuzzy matching."""
    return frozenset(name for name, value in provided.items() if value in record_values(holder, name))


def _candidates(policy_number: str | None, repo: ClaimsRepository) -> Sequence[Policyholder]:
    """V5: narrow to the policy's holders when the policy exists; otherwise ignore it silently."""
    if policy_number:
        holders = repo.policyholders_by_policy(policy_number)
        if holders:
            return holders
    return repo.policyholders()


def evaluate_identity(
    identity: IdentityState, repo: ClaimsRepository, cfg: VerifyConfig
) -> VerificationOutcome:
    """V1: pass only when exactly one record matches at least `min_matches` provided fields."""
    provided = identity.valid_values()
    if len(provided) < cfg.min_matches:
        return NeedMore(missing=cfg.min_matches - len(provided))
    qualified = [
        (holder, matched)
        for holder in _candidates(identity.policy_number, repo)
        if len(matched := matched_fields(holder, provided)) >= cfg.min_matches
    ]
    if len(qualified) == 1:
        holder, matched = qualified[0]
        return Verified(party_id=holder.party_id, matched_fields=matched)
    if len(qualified) > 1:
        return Ambiguous()
    return Failed()


def should_evaluate(identity: IdentityState, cfg: VerifyConfig) -> bool:
    """V2: evaluate only with enough fields and something new since the last evaluation (anti-probing)."""
    return (
        identity.status is IdentityStatus.UNVERIFIED
        and len(identity.valid_values()) >= cfg.min_matches
        and identity.signature() != identity.last_evaluated_signature
    )


def run_identity_check(
    identity: IdentityState,
    caller_role: CallerRole,
    repo: ClaimsRepository,
    cfg: VerifyConfig,
    *,
    turn: int,
    phase: Phase = Phase.VERIFY_ID,
) -> IdentityCheck:
    """Apply V1-V6 to a copy of `identity`.

    A policyholder pass completes verification (VERIFIED). A representative pass only confirms the
    policyholder's identity (IDENTITY_VERIFIED); authorization and consent still follow.
    """
    if identity.status is not IdentityStatus.UNVERIFIED:
        return IdentityCheck(identity=identity, outcome=None)
    if not should_evaluate(identity, cfg):
        provided = len(identity.valid_values())
        missing = max(0, cfg.min_matches - provided)
        return IdentityCheck(identity=identity, outcome=NeedMore(missing) if missing else None)

    outcome = evaluate_identity(identity, repo, cfg)
    new = identity.model_copy(deep=True)
    new.last_evaluated_signature = identity.signature()
    events: list[Event] = []
    if isinstance(outcome, Verified):
        new.party_id = outcome.party_id
        if caller_role is CallerRole.AUTHORIZED_REPRESENTATIVE:
            new.status = IdentityStatus.IDENTITY_VERIFIED
            events.append(Event(type=EventType.IDENTITY_VERIFIED, turn=turn, phase=phase))
        else:
            new.status = IdentityStatus.VERIFIED
            new.verified_as = VerifiedAs.POLICYHOLDER
            events.append(Event(type=EventType.VERIFIED, turn=turn, phase=phase))
    elif isinstance(outcome, Failed):
        new.failed_attempts += 1
        events.append(
            Event(
                type=EventType.VERIFICATION_FAILED,
                turn=turn,
                phase=phase,
                data={"attempt": new.failed_attempts},
            )
        )
        if new.failed_attempts >= cfg.max_failed_attempts:
            new.status = IdentityStatus.LOCKED
            events.append(Event(type=EventType.VERIFICATION_LOCKED, turn=turn, phase=phase))
    return IdentityCheck(identity=new, outcome=outcome, events=events)


def fields_still_needed(identity: IdentityState, cfg: VerifyConfig) -> int:
    return max(0, cfg.min_matches - len(identity.valid_values()))


def suggest_fields(identity: IdentityState) -> list[IdentityField]:
    """Suggestion order: dob, phone, email, id_last4, full_name, skipping provided and declined fields."""
    provided = identity.valid_values()
    return [f for f in SUGGESTION_ORDER if f not in provided and f not in identity.declined]


def is_feasible(identity: IdentityState, cfg: VerifyConfig) -> bool:
    """V8: can the caller still reach `min_matches` with the fields they haven't declined?"""
    return len(identity.valid_values()) + len(suggest_fields(identity)) >= cfg.min_matches
