"""Authorized-representative check (SPEC §8.1.7, A3/A4).

Runs only after the policyholder's identity passed. The result never reveals whether other representatives
exist for the account (A4).
"""

from dataclasses import dataclass
from enum import StrEnum

from sop_agent.data.repository import ClaimsRepository
from sop_agent.domain.enums import AuthorizationStatus
from sop_agent.domain.normalize import normalize_name


class RepresentativeDetail(StrEnum):
    """Details a representative must state before the authorization check can run."""

    NAME = "representative_name"
    RELATIONSHIP = "relationship"


class RelationshipGroup(StrEnum):
    CHILD = "child"
    SPOUSE = "spouse"
    PARENT = "parent"
    SIBLING = "sibling"


RELATIONSHIP_SYNONYMS: dict[RelationshipGroup, frozenset[str]] = {
    RelationshipGroup.CHILD: frozenset({"son", "daughter", "child"}),
    RelationshipGroup.SPOUSE: frozenset({"spouse", "husband", "wife", "partner"}),
    RelationshipGroup.PARENT: frozenset({"mother", "father", "parent"}),
    RelationshipGroup.SIBLING: frozenset({"brother", "sister", "sibling"}),
}


def _clean(text: str) -> str:
    return " ".join(text.strip().casefold().split())


def relationship_key(relationship: str) -> str:
    """Synonym-group name for a known relationship word; otherwise the cleaned word itself (exact match)."""
    word = _clean(relationship)
    for group, words in RELATIONSHIP_SYNONYMS.items():
        if word in words:
            return group.value
    return word


def relationships_compatible(stated: str, on_file: str) -> bool:
    return bool(_clean(stated)) and relationship_key(stated) == relationship_key(on_file)


@dataclass(frozen=True)
class AuthorizationResult:
    status: AuthorizationStatus
    missing: tuple[RepresentativeDetail, ...] = ()


def check_authorization(
    party_id: str,
    representative_name: str | None,
    relationship: str | None,
    repo: ClaimsRepository,
) -> AuthorizationResult:
    """A3: an exact normalized name match plus a compatible relationship; ask for missing details first."""
    representative_name = (representative_name or "").strip()
    relationship = (relationship or "").strip()
    missing = tuple(
        label
        for label, value in (
            (RepresentativeDetail.NAME, representative_name),
            (RepresentativeDetail.RELATIONSHIP, relationship),
        )
        if not value
    )
    if missing:
        return AuthorizationResult(AuthorizationStatus.NEEDS_INFO, missing)
    stated_name = normalize_name(representative_name)
    for record in repo.representatives_for(party_id):
        if (
            stated_name is not None
            and normalize_name(record.rep_name) == stated_name
            and relationships_compatible(relationship, record.relationship)
        ):
            return AuthorizationResult(AuthorizationStatus.MATCHED)
    return AuthorizationResult(AuthorizationStatus.NOT_AUTHORIZED)
