import json
import shutil
from collections.abc import Callable
from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from sop_agent.data.loaders import (
    CLAIMS_FILE,
    GUIDELINE_FILE,
    POLICYHOLDERS_FILE,
    REPRESENTATIVES_FILE,
    build_repository,
    load_repository,
    read_raw_fixtures,
    summarize,
)
from sop_agent.data.repository import InMemoryRepository
from sop_agent.domain.enums import ClaimStatus
from sop_agent.domain.errors import DataLoadError
from tests.helpers import SNAPSHOT_DIR

Records = list[dict[str, Any]]


@pytest.fixture
def fixtures_copy(tmp_path: Path) -> Path:
    target = tmp_path / "fixtures"
    shutil.copytree(SNAPSHOT_DIR, target)
    return target


def _rewrite(directory: Path, file: str, edit: Callable[[Records], Records]) -> None:
    path = directory / file
    data = json.loads(path.read_text(encoding="utf-8"))
    path.write_text(json.dumps(edit(data)), encoding="utf-8")


def _set_first(field: str, value: Any) -> Callable[[Records], Records]:
    def edit(data: Records) -> Records:
        data[0][field] = value
        return data

    return edit


def test_starter_snapshot_loads_with_expected_counts() -> None:
    summary = summarize(load_repository(SNAPSHOT_DIR))
    assert summary.policyholders == 4
    assert summary.claims == 5
    assert summary.representatives == 1
    assert summary.consent_scenarios == ("default", "timeout")
    assert summary.guidance_topics == 6


def test_missing_optional_claim_fields_default_to_none(snapshot_repo: InMemoryRepository) -> None:
    claim = next(c for c in snapshot_repo.claims() if c.status is ClaimStatus.CLOSED)
    assert claim.denial_reason is None
    assert claim.appeal_deadline is None
    assert claim.documents_needed == ()


def test_claim_amounts_and_dates_are_typed(snapshot_repo: InMemoryRepository) -> None:
    claim = next(c for c in snapshot_repo.claims() if c.appeal_deadline is not None)
    assert isinstance(claim.appeal_deadline, date)
    assert isinstance(claim.allowed_max_amount, Decimal)


def test_unknown_status_maps_to_other_and_keeps_raw(merged_repo: InMemoryRepository) -> None:
    other = [c for c in merged_repo.claims() if c.status is ClaimStatus.OTHER]
    assert [c.raw_status for c in other] == ["under_review"]


def test_unknown_case_type_is_kept_lowercased(merged_repo: InMemoryRepository) -> None:
    assert "pet" in {c.case_type for c in merged_repo.claims()}


def test_missing_optional_amounts_are_none(merged_repo: InMemoryRepository) -> None:
    claim = next(c for c in merged_repo.claims() if c.status is ClaimStatus.OTHER)
    assert claim.net_pay is None
    assert claim.net_fee is None


def test_aliases_are_deduplicated_and_exclude_primary(snapshot_repo: InMemoryRepository) -> None:
    for holder in snapshot_repo.policyholders():
        assert holder.phone not in holder.phone_aliases
        assert len(set(holder.phone_aliases)) == len(holder.phone_aliases)
    assert any(h.name_aliases for h in snapshot_repo.policyholders()), "starter data has a name alias"


def test_duplicate_alias_entries_collapse(fixtures_copy: Path) -> None:
    def edit(data: Records) -> Records:
        data[0]["email_aliases"] = ["alt@example.com", "alt@example.com", data[0]["email"]]
        return data

    _rewrite(fixtures_copy, POLICYHOLDERS_FILE, edit)
    holder = load_repository(fixtures_copy).policyholders()[0]
    assert holder.email_aliases == ("alt@example.com",)


def test_guideline_falls_back_to_english_for_missing_language() -> None:
    en = load_repository(SNAPSHOT_DIR, "en").document_guideline()
    fr = load_repository(SNAPSHOT_DIR, "fr").document_guideline()
    assert fr.default_guidance == en.default_guidance
    assert fr.followup_topics == en.followup_topics


def test_guideline_topics_resolve_templates_and_dedupe_phrases(snapshot_repo: InMemoryRepository) -> None:
    guideline = snapshot_repo.document_guideline()
    for topic in guideline.followup_topics:
        assert topic.template
        assert len(set(topic.match_any)) == len(topic.match_any)
    assert "default" in guideline.document_alternative_guidance
    assert guideline.settings
    assert guideline.followup_fallback


def test_glossary_has_amount_field_meanings(snapshot_repo: InMemoryRepository) -> None:
    glossary = snapshot_repo.field_glossary()
    assert set(glossary) == {"expected_reimbursement_amount", "allowed_max_amount", "net_pay", "net_fee"}
    assert all(info.description for info in glossary.values())


def test_missing_required_file_fails_fast_naming_file(fixtures_copy: Path) -> None:
    (fixtures_copy / REPRESENTATIVES_FILE).unlink()
    with pytest.raises(DataLoadError, match=REPRESENTATIVES_FILE):
        load_repository(fixtures_copy)


def test_invalid_json_fails_fast_naming_file(fixtures_copy: Path) -> None:
    (fixtures_copy / CLAIMS_FILE).write_text("[{", encoding="utf-8")
    with pytest.raises(DataLoadError, match=CLAIMS_FILE):
        load_repository(fixtures_copy)


def test_duplicate_party_id_fails_fast_naming_file_and_record(fixtures_copy: Path) -> None:
    _rewrite(fixtures_copy, POLICYHOLDERS_FILE, lambda d: [*d, d[0]])
    raw = read_raw_fixtures(fixtures_copy)
    dup = raw.policyholders[0]["party_id"]
    with pytest.raises(DataLoadError, match=rf"{POLICYHOLDERS_FILE} \[{dup}\].*duplicate"):
        build_repository(raw)


def test_duplicate_case_id_fails_fast(fixtures_copy: Path) -> None:
    _rewrite(fixtures_copy, CLAIMS_FILE, lambda d: [*d, d[0]])
    with pytest.raises(DataLoadError, match=CLAIMS_FILE):
        load_repository(fixtures_copy)


def test_claim_with_unknown_party_fails_fast(fixtures_copy: Path) -> None:
    _rewrite(fixtures_copy, CLAIMS_FILE, _set_first("party_id", "NO-SUCH-PARTY"))
    with pytest.raises(DataLoadError, match=r"claims\.json.*unknown party_id"):
        load_repository(fixtures_copy)


def test_representative_with_unknown_party_fails_fast(fixtures_copy: Path) -> None:
    _rewrite(fixtures_copy, REPRESENTATIVES_FILE, _set_first("buyer_party_id", "NO-SUCH-PARTY"))
    with pytest.raises(DataLoadError, match=REPRESENTATIVES_FILE):
        load_repository(fixtures_copy)


@pytest.mark.parametrize(("field", "value"), [("created_at", "2026-13-01"), ("appeal_deadline", "soon")])
def test_unparseable_claim_date_fails_fast(fixtures_copy: Path, field: str, value: str) -> None:
    _rewrite(fixtures_copy, CLAIMS_FILE, _set_first(field, value))
    with pytest.raises(DataLoadError, match=rf"claims\.json.*{field}"):
        load_repository(fixtures_copy)


def test_unparseable_dob_fails_fast(fixtures_copy: Path) -> None:
    _rewrite(fixtures_copy, POLICYHOLDERS_FILE, _set_first("dob", "March 15"))
    with pytest.raises(DataLoadError, match=POLICYHOLDERS_FILE):
        load_repository(fixtures_copy)


def test_unparseable_amount_fails_fast(fixtures_copy: Path) -> None:
    _rewrite(fixtures_copy, CLAIMS_FILE, _set_first("net_pay", "a lot"))
    with pytest.raises(DataLoadError, match=r"claims\.json.*net_pay"):
        load_repository(fixtures_copy)


def test_missing_required_field_fails_fast(fixtures_copy: Path) -> None:
    def edit(data: Records) -> Records:
        del data[0]["email"]
        return data

    _rewrite(fixtures_copy, POLICYHOLDERS_FILE, edit)
    with pytest.raises(DataLoadError, match=r"policyholders\.json.*email"):
        load_repository(fixtures_copy)


def test_guideline_text_missing_in_every_language_fails_fast() -> None:
    raw = read_raw_fixtures(SNAPSHOT_DIR)
    broken = replace(raw, guideline={**raw.guideline, "default_guidance": {"de": "..."}})
    with pytest.raises(DataLoadError, match=GUIDELINE_FILE):
        build_repository(broken)


def test_unknown_extra_record_keys_are_ignored(fixtures_copy: Path) -> None:
    _rewrite(fixtures_copy, CLAIMS_FILE, _set_first("loyalty_tier", "gold"))
    assert len(load_repository(fixtures_copy).claims()) == 5
