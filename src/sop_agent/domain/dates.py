"""Date hints and deadline status (SPEC §8.2.3, §8.3.5).

Code does all date math; the LLM only restates the results (principle 9). Every function takes `today` and
never reads the system clock.
"""

import calendar
from datetime import date
from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class DateHint(BaseModel):
    """A partial date the caller mentioned ("January", "last March", "2026-01-12"); each part is optional."""

    model_config = ConfigDict(frozen=True)

    year: int | None = None
    month: int | None = None
    day: int | None = None

    def is_empty(self) -> bool:
        return self.year is None and self.month is None and self.day is None


class DateRange(BaseModel):
    model_config = ConfigDict(frozen=True)

    start: date
    end: date

    def contains(self, value: date) -> bool:
        return self.start <= value <= self.end


def _valid_month(month: int | None) -> bool:
    return month is not None and 1 <= month <= 12


def _safe_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def hint_range(hint: DateHint, today: date) -> DateRange | None:
    """Turn a hint into the date range it refers to, or None if it's empty, invalid or in the future.

    A month without a year means the most recent occurrence on or before today.
    """
    if hint.is_empty() or (hint.month is not None and not _valid_month(hint.month)):
        return None
    if hint.month is None:
        if hint.year is None or hint.day is not None:
            return None
        start, end = date(hint.year, 1, 1), date(hint.year, 12, 31)
    else:
        year = hint.year if hint.year is not None else _latest_year_for(hint.month, hint.day, today)
        if hint.day is not None:
            day = _safe_date(year, hint.month, hint.day)
            if day is None:
                return None
            start = end = day
        else:
            start = date(year, hint.month, 1)
            end = date(year, hint.month, calendar.monthrange(year, hint.month)[1])
    if start > today:
        return None
    return DateRange(start=start, end=min(end, today))


def _latest_year_for(month: int, day: int | None, today: date) -> int:
    first = date(today.year, month, day if day is not None and _safe_date(today.year, month, day) else 1)
    return today.year if first <= today else today.year - 1


class DeadlineState(StrEnum):
    NONE = "none"
    OPEN = "open"
    TODAY = "today"
    PASSED = "passed"


class DeadlineStatus(BaseModel):
    """Computed deadline facts placed in grounding so the LLM never does date math."""

    model_config = ConfigDict(frozen=True)

    deadline: date | None
    state: DeadlineState
    days_remaining: int | None


def deadline_status(deadline: date | None, today: date) -> DeadlineStatus:
    if deadline is None:
        return DeadlineStatus(deadline=None, state=DeadlineState.NONE, days_remaining=None)
    days = (deadline - today).days
    if days > 0:
        state = DeadlineState.OPEN
    elif days == 0:
        state = DeadlineState.TODAY
    else:
        state = DeadlineState.PASSED
    return DeadlineStatus(deadline=deadline, state=state, days_remaining=max(days, 0))
