from datetime import date

import pytest

from sop_agent.data.repository import InMemoryRepository
from sop_agent.domain.dates import DateHint
from sop_agent.domain.enums import ClaimStatus, Path
from sop_agent.memory.state import CaseHints, IntentCandidate
from sop_agent.sop.resolver import (
    Ambiguous,
    NoClaims,
    NoMatch,
    Unique,
    canonical_case_type,
    contradicts,
    infer_path,
    rank,
    resolve,
    status_from_word,
)

TODAY = date(2026, 3, 10)
MARGARET_HINTS = CaseHints(case_type="healthcare", status=ClaimStatus.DENIED, date_hint=DateHint(month=1))


def test_margaret_hints_resolve_cl2048_uniquely_with_the_expected_scores(
    snapshot_repo: InMemoryRepository,
) -> None:
    claims = snapshot_repo.claims_for("P9")
    scores = {s.claim.case_id: s.score for s in rank(claims, MARGARET_HINTS, TODAY)}
    assert scores == {"CL-2048": 6, "CL-2011": 0, "CL-1899": -6, "CL-2102": -6}
    result = resolve(claims, MARGARET_HINTS, set(), TODAY)
    assert isinstance(result, Unique)
    assert result.claim.case_id == "CL-2048"


def test_p91_january_denials_are_ambiguous(merged_repo: InMemoryRepository) -> None:
    result = resolve(merged_repo.claims_for("P91"), MARGARET_HINTS, set(), TODAY)
    assert isinstance(result, Ambiguous)
    assert {c.case_id for c in result.candidates} == {"CL-9101", "CL-9102"}


def test_exact_case_id_wins(merged_repo: InMemoryRepository) -> None:
    hints = MARGARET_HINTS.model_copy(update={"case_id": "CL-9102"})
    result = resolve(merged_repo.claims_for("P91"), hints, set(), TODAY)
    assert isinstance(result, Unique)
    assert result.claim.case_id == "CL-9102"


def test_status_contradiction_applies_only_to_stated_hints(snapshot_repo: InMemoryRepository) -> None:
    claims = snapshot_repo.claims_for("P9")
    with_status = {s.claim.case_id: s.score for s in rank(claims, CaseHints(status=ClaimStatus.OPEN), TODAY)}
    assert with_status["CL-2102"] == 2
    assert with_status["CL-2048"] == -3
    no_status = {s.claim.case_id: s.score for s in rank(claims, CaseHints(case_type="auto"), TODAY)}
    assert no_status["CL-2102"] == 2
    assert no_status["CL-1899"] == -3  # type contradiction only; no status penalty


def test_month_only_hint_scores_most_recent_occurrence(snapshot_repo: InMemoryRepository) -> None:
    claims = snapshot_repo.claims_for("P9")
    scores = {s.claim.case_id: s.score for s in rank(claims, CaseHints(date_hint=DateHint(month=1)), TODAY)}
    assert scores["CL-2048"] == 2  # January 2026
    assert scores["CL-2011"] == 1  # January 2025: same month, no year given
    assert scores["CL-1899"] == 0


def test_explicit_year_gets_no_same_month_bonus(snapshot_repo: InMemoryRepository) -> None:
    claims = snapshot_repo.claims_for("P9")
    hints = CaseHints(date_hint=DateHint(year=2025, month=1))
    scores = {s.claim.case_id: s.score for s in rank(claims, hints, TODAY)}
    assert (scores["CL-2011"], scores["CL-2048"]) == (2, 0)


def test_keyword_overlap_adds_one(merged_repo: InMemoryRepository) -> None:
    hints = MARGARET_HINTS.model_copy(update={"keywords": ["x-ray"]})
    scores = {s.claim.case_id: s.score for s in rank(merged_repo.claims_for("P91"), hints, TODAY)}
    assert scores == {"CL-9101": 7, "CL-9102": 6}


def test_no_hints_is_no_match_listing_three_most_recent(snapshot_repo: InMemoryRepository) -> None:
    result = resolve(snapshot_repo.claims_for("P9"), CaseHints(), set(), TODAY)
    assert isinstance(result, NoMatch)
    assert [c.case_id for c in result.recent] == ["CL-2102", "CL-2048", "CL-1899"]


def test_single_claim_is_unique_without_hints(snapshot_repo: InMemoryRepository) -> None:
    result = resolve(snapshot_repo.claims_for("P12"), CaseHints(), set(), TODAY)
    assert isinstance(result, Unique)
    assert result.claim.case_id == "CL-3001"


def test_single_claim_with_contradicting_hint_is_not_auto_resolved(snapshot_repo: InMemoryRepository) -> None:
    result = resolve(snapshot_repo.claims_for("P12"), CaseHints(case_type="auto"), set(), TODAY)
    assert not isinstance(result, Unique)


def test_excluded_claims_are_skipped(snapshot_repo: InMemoryRepository) -> None:
    result = resolve(snapshot_repo.claims_for("P9"), MARGARET_HINTS, {"CL-2048"}, TODAY)
    assert not (isinstance(result, Unique) and result.claim.case_id == "CL-2048")
    assert isinstance(resolve(snapshot_repo.claims_for("P12"), CaseHints(), {"CL-3001"}, TODAY), NoClaims)


def test_unknown_status_claim_gets_no_status_penalty(merged_repo: InMemoryRepository) -> None:
    [scored] = rank(merged_repo.claims_for("P92"), CaseHints(status=ClaimStatus.DENIED), TODAY)
    assert scored.score == 0


@pytest.mark.parametrize(
    ("word", "canonical"), [("Medical", "healthcare"), ("car", "auto"), ("dentist", "dental"), ("pet", "pet")]
)
def test_case_type_synonyms(word: str, canonical: str) -> None:
    assert canonical_case_type(word) == canonical


@pytest.mark.parametrize(
    ("word", "status"),
    [
        ("settled", ClaimStatus.CLOSED),
        ("in progress", ClaimStatus.OPEN),
        ("rejected", ClaimStatus.DENIED),
        ("lost", None),
    ],
)
def test_status_synonyms(word: str, status: ClaimStatus | None) -> None:
    assert status_from_word(word) is status


def test_path_inference_prefers_confident_intent(snapshot_repo: InMemoryRepository) -> None:
    claim = snapshot_repo.claim("CL-2048")
    assert claim is not None
    confident = [IntentCandidate(path=Path.DOCUMENT_SUBMISSION, confidence=0.9, last_turn=1)]
    assert infer_path(confident, claim) is Path.DOCUMENT_SUBMISSION
    weak = [IntentCandidate(path=Path.DOCUMENT_SUBMISSION, confidence=0.5, last_turn=1)]
    assert infer_path(weak, claim) is Path.DENIAL_QUESTION
    general = [IntentCandidate(path=Path.GENERAL_INSURANCE_QUESTION, confidence=0.9, last_turn=1)]
    assert infer_path(general, claim) is Path.DENIAL_QUESTION


def test_path_default_by_status(snapshot_repo: InMemoryRepository) -> None:
    for case_id, path in (
        ("CL-2102", Path.STATUS_INQUIRY),
        ("CL-2011", Path.STATUS_INQUIRY),
        ("CL-2048", Path.DENIAL_QUESTION),
    ):
        claim = snapshot_repo.claim(case_id)
        assert claim is not None
        assert infer_path([], claim) is path


def test_contradicts_detects_claim_switch(snapshot_repo: InMemoryRepository) -> None:
    claim = snapshot_repo.claim("CL-2048")
    assert claim is not None
    assert contradicts(claim, "CL-2102", None, None)
    assert contradicts(claim, None, "dental", None)
    assert contradicts(claim, None, None, ClaimStatus.OPEN)
    assert not contradicts(claim, "cl-2048", "medical", ClaimStatus.DENIED)
    assert not contradicts(claim, None, None, None)


def test_single_weak_candidate_is_offered_for_confirmation_not_auto_resolved(
    snapshot_repo: InMemoryRepository,
) -> None:
    result = resolve(snapshot_repo.claims_for("P9"), CaseHints(case_type="auto"), set(), TODAY)
    assert isinstance(result, Ambiguous)
    assert [c.case_id for c in result.candidates] == ["CL-2102"]
