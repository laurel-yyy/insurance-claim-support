"""Claim scoring, resolution and path inference (SPEC §8.2.2-§8.2.5).

Scores only the verified policyholder's own claims; callers pass them in, so ownership is decided upstream.
A contradiction penalty applies only to a hint the caller actually stated.
"""

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from enum import StrEnum

from sop_agent.domain.dates import hint_range
from sop_agent.domain.enums import PATHS_NEEDING_CLAIM, ClaimStatus, Path
from sop_agent.domain.models import Claim
from sop_agent.memory.state import CaseHints, IntentCandidate

CASE_ID_SCORE = 10
MATCH_SCORE = 2
CONTRADICTION_PENALTY = -3
SAME_MONTH_OTHER_YEAR_SCORE = 1
KEYWORD_SCORE = 1
KEYWORD_OVERLAP = 0.5
UNIQUE_MIN_SCORE = 4
UNIQUE_MIN_LEAD = 3
CANDIDATE_MIN_SCORE = 2
MAX_CANDIDATES = 3
PATH_MIN_CONFIDENCE = 0.6

CASE_TYPE_SYNONYMS: dict[str, str] = {
    "medical": "healthcare",
    "health": "healthcare",
    "health care": "healthcare",
    "car": "auto",
    "vehicle": "auto",
    "automobile": "auto",
    "dentist": "dental",
    "teeth": "dental",
}

STATUS_SYNONYMS: dict[str, ClaimStatus] = {
    "settled": ClaimStatus.CLOSED,
    "paid": ClaimStatus.CLOSED,
    "completed": ClaimStatus.CLOSED,
    "complete": ClaimStatus.CLOSED,
    "closed": ClaimStatus.CLOSED,
    "pending": ClaimStatus.OPEN,
    "in progress": ClaimStatus.OPEN,
    "processing": ClaimStatus.OPEN,
    "open": ClaimStatus.OPEN,
    "rejected": ClaimStatus.DENIED,
    "declined": ClaimStatus.DENIED,
    "denied": ClaimStatus.DENIED,
}

DEFAULT_PATH_BY_STATUS: dict[ClaimStatus, Path] = {
    ClaimStatus.DENIED: Path.DENIAL_QUESTION,
    ClaimStatus.OPEN: Path.STATUS_INQUIRY,
    ClaimStatus.CLOSED: Path.STATUS_INQUIRY,
    ClaimStatus.OTHER: Path.STATUS_INQUIRY,
}

_STOPWORDS = frozenset({"a", "an", "the", "my", "of", "and", "for", "to", "on", "in", "with", "claim"})


def canonical_case_type(value: str) -> str:
    text = " ".join(value.strip().casefold().split())
    return CASE_TYPE_SYNONYMS.get(text, text)


def status_from_word(word: str) -> ClaimStatus | None:
    """Map what callers say ("settled", "in progress") to a status (§6.2.5)."""
    return STATUS_SYNONYMS.get(" ".join(word.strip().casefold().split()))


def tokens(text: str) -> set[str]:
    """Lowercased word tokens, naively singularized, without stopwords."""
    words = re.findall(r"[a-z0-9]+", text.casefold())
    return {_singular(w) for w in words if w not in _STOPWORDS}


def _singular(word: str) -> str:
    return word[:-1] if len(word) > 3 and word.endswith("s") and not word.endswith("ss") else word


@dataclass(frozen=True)
class ScoredClaim:
    claim: Claim
    score: int
    contradictions: int


def score_claim(claim: Claim, hints: CaseHints, today: date) -> ScoredClaim:
    score, contradictions = 0, 0
    if hints.case_id and hints.case_id == claim.case_id.upper():
        score += CASE_ID_SCORE
    if hints.case_type:
        if canonical_case_type(hints.case_type) == canonical_case_type(claim.case_type):
            score += MATCH_SCORE
        else:
            score, contradictions = score + CONTRADICTION_PENALTY, contradictions + 1
    if hints.status is not None and claim.status is not ClaimStatus.OTHER:
        if hints.status is claim.status:
            score += MATCH_SCORE
        else:
            score, contradictions = score + CONTRADICTION_PENALTY, contradictions + 1
    score += _date_score(claim, hints, today)
    if hints.keywords and _keyword_overlap(hints.keywords, claim) >= KEYWORD_OVERLAP:
        score += KEYWORD_SCORE
    return ScoredClaim(claim=claim, score=score, contradictions=contradictions)


def _date_score(claim: Claim, hints: CaseHints, today: date) -> int:
    hint = hints.date_hint
    if hint is None:
        return 0
    window = hint_range(hint, today)
    if window is not None and window.contains(claim.created_at):
        return MATCH_SCORE
    if hint.year is None and hint.month == claim.created_at.month:
        return SAME_MONTH_OTHER_YEAR_SCORE
    return 0


def _keyword_overlap(keywords: Iterable[str], claim: Claim) -> float:
    wanted = set().union(*(tokens(k) for k in keywords))
    if not wanted:
        return 0.0
    text = " ".join([claim.summary, claim.denial_reason or "", *claim.documents_needed])
    return len(wanted & tokens(text)) / len(wanted)


class ResolutionMode(StrEnum):
    """How a claim was selected; AUTO means the reply must name it so the caller can correct it (§8.2.5)."""

    AUTO = "auto"
    CHOSEN = "chosen"


@dataclass(frozen=True)
class Unique:
    claim: Claim


@dataclass(frozen=True)
class Ambiguous:
    candidates: tuple[Claim, ...]


@dataclass(frozen=True)
class NoMatch:
    recent: tuple[Claim, ...]


@dataclass(frozen=True)
class NoClaims:
    pass


Resolution = Unique | Ambiguous | NoMatch | NoClaims


def most_recent(claims: Iterable[Claim], limit: int = MAX_CANDIDATES) -> tuple[Claim, ...]:
    return tuple(sorted(claims, key=lambda c: (c.created_at, c.case_id), reverse=True)[:limit])


def rank(claims: Sequence[Claim], hints: CaseHints, today: date) -> list[ScoredClaim]:
    scored = [score_claim(c, hints, today) for c in claims]
    return sorted(scored, key=lambda s: (s.score, s.claim.created_at, s.claim.case_id), reverse=True)


def resolve(claims: Sequence[Claim], hints: CaseHints, excluded: set[str], today: date) -> Resolution:
    """§8.2.2 decision: unique high confidence, ambiguous candidates, or no match."""
    pool = [c for c in claims if c.case_id not in excluded]
    if not pool:
        return NoClaims()
    ranked = rank(pool, hints, today)
    top = ranked[0]
    if len(pool) == 1 and top.contradictions == 0:
        return Unique(top.claim)
    runner_up = ranked[1].score if len(ranked) > 1 else None
    if top.score >= UNIQUE_MIN_SCORE and (runner_up is None or top.score - runner_up >= UNIQUE_MIN_LEAD):
        return Unique(top.claim)
    candidates = tuple(s.claim for s in ranked if s.score >= CANDIDATE_MIN_SCORE)[:MAX_CANDIDATES]
    if candidates:
        return Ambiguous(candidates)
    return NoMatch(most_recent(pool))


def infer_path(intents: Iterable[IntentCandidate], claim: Claim) -> Path:
    """§8.2.4: the most confident claim-related intent (≥ 0.6), else a default from the claim status."""
    confident = [i for i in intents if i.path in PATHS_NEEDING_CLAIM and i.confidence >= PATH_MIN_CONFIDENCE]
    if confident:
        return max(confident, key=lambda i: (i.confidence, i.last_turn)).path
    return DEFAULT_PATH_BY_STATUS[claim.status]


def contradicts(claim: Claim, case_id: str | None, case_type: str | None, status: ClaimStatus | None) -> bool:
    """Do hints stated this turn point away from the selected claim (§8.2.5 claim switching)?"""
    if case_id and case_id.strip().upper() != claim.case_id.upper():
        return True
    if case_type and canonical_case_type(case_type) != canonical_case_type(claim.case_type):
        return True
    return status is not None and claim.status is not ClaimStatus.OTHER and status is not claim.status
