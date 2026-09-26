from datetime import date

import pytest

from sop_agent.domain.dates import DateHint, DateRange, DeadlineState, deadline_status, hint_range

TODAY = date(2026, 3, 10)


@pytest.mark.parametrize(
    ("deadline", "state", "days"),
    [
        (date(2026, 3, 18), DeadlineState.OPEN, 8),
        (date(2026, 3, 11), DeadlineState.OPEN, 1),
        (date(2026, 3, 10), DeadlineState.TODAY, 0),
        (date(2026, 3, 9), DeadlineState.PASSED, 0),
        (None, DeadlineState.NONE, None),
    ],
)
def test_deadline_status_is_computed_by_code(
    deadline: date | None, state: DeadlineState, days: int | None
) -> None:
    status = deadline_status(deadline, TODAY)
    assert (status.deadline, status.state, status.days_remaining) == (deadline, state, days)


def _range(start: date, end: date) -> DateRange:
    return DateRange(start=start, end=end)


@pytest.mark.parametrize(
    ("hint", "expected"),
    [
        (DateHint(month=1), _range(date(2026, 1, 1), date(2026, 1, 31))),
        (DateHint(month=11), _range(date(2025, 11, 1), date(2025, 11, 30))),
        (DateHint(month=3), _range(date(2026, 3, 1), date(2026, 3, 10))),
        (DateHint(month=2), _range(date(2026, 2, 1), date(2026, 2, 28))),
        (DateHint(year=2025, month=1), _range(date(2025, 1, 1), date(2025, 1, 31))),
        (DateHint(year=2026, month=1, day=12), _range(date(2026, 1, 12), date(2026, 1, 12))),
        (DateHint(month=3, day=15), _range(date(2025, 3, 15), date(2025, 3, 15))),
        (DateHint(year=2025), _range(date(2025, 1, 1), date(2025, 12, 31))),
    ],
)
def test_hint_range_month_only_means_most_recent_occurrence(hint: DateHint, expected: DateRange) -> None:
    assert hint_range(hint, TODAY) == expected


@pytest.mark.parametrize(
    "hint",
    [
        DateHint(),
        DateHint(month=13),
        DateHint(year=2026, month=4),
        DateHint(year=2027),
        DateHint(year=2026, month=2, day=30),
        DateHint(day=5),
    ],
)
def test_hint_range_rejects_empty_invalid_or_future_hints(hint: DateHint) -> None:
    assert hint_range(hint, TODAY) is None
