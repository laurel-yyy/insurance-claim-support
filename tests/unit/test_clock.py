from datetime import date

from sop_agent.config import Settings
from sop_agent.container import Container
from sop_agent.domain.clock import FixedClock, SystemClock
from tests.helpers import SNAPSHOT_DIR


def test_fixed_clock_returns_pinned_date_for_today_and_now() -> None:
    clock = FixedClock(date(2026, 3, 10))
    assert clock.today() == date(2026, 3, 10)
    assert clock.now().date() == date(2026, 3, 10)
    assert clock.now().tzinfo is not None


def test_container_uses_fixed_clock_when_demo_today_is_set() -> None:
    settings = Settings(_env_file=None, fixtures_dir=SNAPSHOT_DIR, demo_today=date(2026, 3, 10))
    container = Container.build(settings)
    assert isinstance(container.clock, FixedClock)
    assert container.clock.today() == date(2026, 3, 10)


def test_container_uses_system_clock_when_demo_today_is_empty() -> None:
    settings = Settings(_env_file=None, fixtures_dir=SNAPSHOT_DIR, demo_today=None)
    assert isinstance(Container.build(settings).clock, SystemClock)
