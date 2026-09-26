from decimal import Decimal

import pytest

from sop_agent.domain.errors import AmountParseError
from sop_agent.domain.money import format_usd, parse_amount


def test_parse_amount_reads_decimal_strings() -> None:
    assert parse_amount("1450.00") == Decimal("1450.00")
    assert parse_amount(" $1,450.00 ") == Decimal("1450.00")


@pytest.mark.parametrize("raw", ["", "abc", "12..0", "NaN", "Infinity"])
def test_parse_amount_rejects_non_amounts(raw: str) -> None:
    with pytest.raises(AmountParseError):
        parse_amount(raw)


@pytest.mark.parametrize(
    ("amount", "expected"),
    [("1450", "$1,450.00"), ("0.00", "$0.00"), ("3200.5", "$3,200.50"), ("-12.5", "-$12.50")],
)
def test_format_usd_uses_grouping_and_two_decimals(amount: str, expected: str) -> None:
    assert format_usd(Decimal(amount)) == expected
