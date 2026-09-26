from sop_agent.data.repository import InMemoryRepository


def test_policyholder_lookup_hit_and_miss(snapshot_repo: InMemoryRepository) -> None:
    first = snapshot_repo.policyholders()[0]
    assert snapshot_repo.policyholder(first.party_id) == first
    assert snapshot_repo.policyholder("does-not-exist") is None


def test_policyholders_by_policy_is_case_insensitive(snapshot_repo: InMemoryRepository) -> None:
    first = snapshot_repo.policyholders()[0]
    assert snapshot_repo.policyholders_by_policy(first.policy_number.lower()) == (first,)
    assert snapshot_repo.policyholders_by_policy("POL-0000000") == ()


def test_claims_for_returns_only_that_partys_claims(snapshot_repo: InMemoryRepository) -> None:
    for holder in snapshot_repo.policyholders():
        assert all(c.party_id == holder.party_id for c in snapshot_repo.claims_for(holder.party_id))
    total = sum(len(snapshot_repo.claims_for(h.party_id)) for h in snapshot_repo.policyholders())
    assert total == len(snapshot_repo.claims())


def test_policyholder_without_claims_returns_empty(snapshot_repo: InMemoryRepository) -> None:
    empty = [h for h in snapshot_repo.policyholders() if not snapshot_repo.claims_for(h.party_id)]
    assert empty, "starter data contains policyholders with no claims"


def test_claim_lookup_hit_and_miss(snapshot_repo: InMemoryRepository) -> None:
    claim = snapshot_repo.claims()[0]
    assert snapshot_repo.claim(claim.case_id) == claim
    assert snapshot_repo.claim("CL-does-not-exist") is None


def test_representatives_for_matches_buyer(snapshot_repo: InMemoryRepository) -> None:
    rep = snapshot_repo.representatives()[0]
    assert snapshot_repo.representatives_for(rep.buyer_party_id) == (rep,)
    others = [h for h in snapshot_repo.policyholders() if h.party_id != rep.buyer_party_id]
    assert all(snapshot_repo.representatives_for(h.party_id) == () for h in others)


def test_consent_scenario_hit_and_miss(snapshot_repo: InMemoryRepository) -> None:
    default = snapshot_repo.consent_scenario("default")
    assert default is not None
    assert default.status_sequence == ("pending", "approved")
    assert snapshot_repo.consent_scenario("nope") is None


def test_merged_repository_adds_edge_case_records(merged_repo: InMemoryRepository) -> None:
    ids = {h.party_id for h in merged_repo.policyholders()}
    assert {"P90", "P91", "P92"} <= ids
    declined = merged_repo.consent_scenario("declined")
    assert declined is not None
    assert declined.status_sequence[-1] == "declined"
    assert [r.relationship for r in merged_repo.representatives_for("P91")] == ["spouse"]


def test_merged_repository_has_two_people_with_the_same_name(merged_repo: InMemoryRepository) -> None:
    names = [h.name for h in merged_repo.policyholders()]
    duplicated = {n for n in names if names.count(n) > 1}
    assert len(duplicated) == 1
    same = [h for h in merged_repo.policyholders() if h.name in duplicated]
    assert len({h.policy_number for h in same}) == 2
    assert len({h.dob for h in same}) == 2


def test_merged_repository_has_ambiguous_january_denials(merged_repo: InMemoryRepository) -> None:
    claims = merged_repo.claims_for("P91")
    assert len(claims) == 2
    assert {(c.case_type, c.status.value, c.created_at.year, c.created_at.month) for c in claims} == {
        ("healthcare", "denied", 2026, 1)
    }


def test_repository_returns_immutable_sequences(snapshot_repo: InMemoryRepository) -> None:
    assert isinstance(snapshot_repo.policyholders(), tuple)
    assert isinstance(snapshot_repo.claims_for(snapshot_repo.policyholders()[0].party_id), tuple)
