import pytest

from sop_agent.data.repository import InMemoryRepository
from sop_agent.domain.enums import AuthorizationStatus
from sop_agent.sop.authorization import (
    RepresentativeDetail,
    check_authorization,
    relationship_key,
    relationships_compatible,
)


@pytest.mark.parametrize("relationship", ["son", "child", "Son", " daughter "])
def test_a3_david_chen_as_son_or_child_matches_p9(
    snapshot_repo: InMemoryRepository, relationship: str
) -> None:
    result = check_authorization("P9", "David Chen", relationship, snapshot_repo)
    assert result.status is AuthorizationStatus.MATCHED


def test_a3_representative_name_is_normalized(snapshot_repo: InMemoryRepository) -> None:
    assert (
        check_authorization("P9", "chen, david", "son", snapshot_repo).status is AuthorizationStatus.MATCHED
    )


@pytest.mark.parametrize("relationship", ["spouse", "brother", "friend"])
def test_a3_right_name_wrong_relationship_does_not_match(
    snapshot_repo: InMemoryRepository, relationship: str
) -> None:
    result = check_authorization("P9", "David Chen", relationship, snapshot_repo)
    assert result.status is AuthorizationStatus.NOT_AUTHORIZED


def test_a3_wrong_name_does_not_match(snapshot_repo: InMemoryRepository) -> None:
    assert (
        check_authorization("P9", "Daniel Chen", "son", snapshot_repo).status
        is AuthorizationStatus.NOT_AUTHORIZED
    )


@pytest.mark.parametrize("relationship", ["spouse", "husband", "wife", "partner"])
def test_a3_spouse_synonyms_match_for_p91(merged_repo: InMemoryRepository, relationship: str) -> None:
    [record] = merged_repo.representatives_for("P91")
    assert (
        check_authorization("P91", record.rep_name, relationship, merged_repo).status
        is AuthorizationStatus.MATCHED
    )


def test_a4_no_representative_record_is_not_authorized(snapshot_repo: InMemoryRepository) -> None:
    assert (
        check_authorization("P12", "David Chen", "son", snapshot_repo).status
        is AuthorizationStatus.NOT_AUTHORIZED
    )


def test_a4_representative_registered_for_another_policyholder_is_not_authorized(
    snapshot_repo: InMemoryRepository,
) -> None:
    assert (
        check_authorization("P13", "David Chen", "son", snapshot_repo).status
        is AuthorizationStatus.NOT_AUTHORIZED
    )


@pytest.mark.parametrize(
    ("name", "relationship", "missing"),
    [
        (None, "son", (RepresentativeDetail.NAME,)),
        ("David Chen", "", (RepresentativeDetail.RELATIONSHIP,)),
        ("  ", None, (RepresentativeDetail.NAME, RepresentativeDetail.RELATIONSHIP)),
    ],
)
def test_a3_missing_details_are_requested_first(
    snapshot_repo: InMemoryRepository,
    name: str | None,
    relationship: str | None,
    missing: tuple[RepresentativeDetail, ...],
) -> None:
    result = check_authorization("P9", name, relationship, snapshot_repo)
    assert result.status is AuthorizationStatus.NEEDS_INFO
    assert result.missing == missing


def test_a3_relationship_groups() -> None:
    assert relationship_key("husband") == relationship_key("wife") == "spouse"
    assert relationship_key("mother") == relationship_key("parent")
    assert relationship_key("sister") == relationship_key("sibling")
    assert relationship_key("son") != relationship_key("father")


def test_a3_unknown_relationship_words_must_match_exactly() -> None:
    assert relationships_compatible("guardian", "Guardian")
    assert not relationships_compatible("guardian", "caregiver")
    assert not relationships_compatible("", "son")
