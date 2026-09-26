"""Amount parsing and formatting. Code formats money so the LLM only restates it (principle 9)."""

from decimal import Decimal, InvalidOperation

from sop_agent.domain.errors import AmountParseError


def parse_amount(raw: str) -> Decimal:
    """Parse a decimal-string amount such as "1450.00"; reject anything that is not a finite number."""
    text = raw.strip().replace(",", "").removeprefix("$")
    try:
        value = Decimal(text)
    except InvalidOperation as exc:
        raise AmountParseError(f"not a decimal amount: {raw!r}") from exc
    if not value.is_finite():
        raise AmountParseError(f"not a finite amount: {raw!r}")
    return value


def format_usd(amount: Decimal) -> str:
    """Render an amount as "$1,450.00" (negative as "-$12.50")."""
    quantized = amount.quantize(Decimal("0.01"))
    sign = "-" if quantized < 0 else ""
    return f"{sign}${abs(quantized):,.2f}"
