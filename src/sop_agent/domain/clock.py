"""Injected time source. Nothing in sop/ or domain/ may read the system clock directly (§3.2)."""

from datetime import UTC, date, datetime
from typing import Protocol


class Clock(Protocol):
    """Source of "today" and "now" so rules stay deterministic and testable."""

    def today(self) -> date: ...

    def now(self) -> datetime: ...


class SystemClock:
    """Real wall-clock time, used when DEMO_TODAY is empty."""

    def today(self) -> date:
        return datetime.now(UTC).date()

    def now(self) -> datetime:
        return datetime.now(UTC)


class FixedClock:
    """Pinned date, so the demo data stays internally consistent (§6.2.7) and tests are reproducible.

    `now()` keeps the real time of day on the fixed date, so timestamps still move within a session.
    """

    def __init__(self, fixed: date) -> None:
        self._fixed = fixed

    def today(self) -> date:
        return self._fixed

    def now(self) -> datetime:
        return datetime.combine(self._fixed, datetime.now(UTC).time(), UTC)
