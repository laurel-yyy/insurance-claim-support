from decimal import Decimal

import pytest

from sop_agent.agent.sensitive_index import SensitiveIndex, TokenKind, amounts_in, dates_in
from sop_agent.data.repository import InMemoryRepository


@pytest.fixture
def index(snapshot_repo: InMemoryRepository) -> SensitiveIndex:
    return SensitiveIndex(snapshot_repo)


def _kinds(index: SensitiveIndex, text: str) -> set[TokenKind]:
    return {h.kind for h in index.find(text)}


@pytest.mark.parametrize("text", ["$1,450.00", "1450.00", "$1,450", "1,450", "the fee was $1450"])
def test_amount_forms_are_recognized(index: SensitiveIndex, text: str) -> None:
    assert TokenKind.AMOUNT in _kinds(index, text)


def test_zero_and_plain_numbers_are_not_indexed_amounts(index: SensitiveIndex) -> None:
    assert TokenKind.AMOUNT not in _kinds(index, "$0.00 and 1450 and call 1-800-555-0100")


@pytest.mark.parametrize("text", ["2026-01-12", "January 12, 2026", "1/12/2026"])
def test_full_date_forms(index: SensitiveIndex, text: str) -> None:
    assert TokenKind.DATE in _kinds(index, text)


@pytest.mark.parametrize("text", ["January 12", "Jan 12th"])
def test_month_day_forms(index: SensitiveIndex, text: str) -> None:
    assert TokenKind.MONTH_DAY in _kinds(index, text)


def test_month_only_is_not_a_token(index: SensitiveIndex) -> None:
    assert _kinds(index, "your claim from January") == set()


def test_ids_emails_phones_names_and_denial_phrases(index: SensitiveIndex) -> None:
    text = (
        "CL-2048 on POL-9921 for Margaret Chen, margaret@email.com, 650.521.2836: "
        "the review file did not include the pathology report"
    )
    kinds = _kinds(index, text)
    assert {TokenKind.ID, TokenKind.EMAIL, TokenKind.PHONE, TokenKind.NAME, TokenKind.PHRASE} <= kinds


def test_hits_record_the_owning_party(index: SensitiveIndex) -> None:
    [hit] = [h for h in index.find("claim CL-3001") if h.kind is TokenKind.ID]
    assert hit.parties == {"P12"}


def test_alias_name_and_representative_are_indexed(index: SensitiveIndex) -> None:
    assert TokenKind.NAME in _kinds(index, "Yaven Li")
    assert TokenKind.NAME in _kinds(index, "David Chen")


def test_document_names_and_case_types_are_not_tokens(index: SensitiveIndex) -> None:
    assert _kinds(index, "a healthcare claim needs a pathology report and an office note") == set()


def test_helpers_normalize_forms() -> None:
    assert amounts_in("$1,450 and 780.00") == {Decimal("1450.00"), Decimal("780.00")}
    full, month_day = dates_in("March 18, 2026 and 3/18/2026 and April 2")
    assert full == {"2026-03-18"} and month_day == {"03-18", "04-02"}
