"""One real Extractor call (SPEC §17 M3). Skipped unless ANTHROPIC_API_KEY is set (env or .env)."""

from datetime import date

import pytest

from sop_agent.config import Settings
from sop_agent.container import Container
from sop_agent.domain.dates import DateHint
from sop_agent.domain.enums import ClaimStatus, IdentityField, Path
from tests.builders import new_state
from tests.helpers import SNAPSHOT_DIR

MARGARET = (
    "I'm the policyholder. My name is Margaret Chen, policy POL-9921. I'm calling about my denied healthcare "
    "claim from January. DOB is 1985-03-15, SSN last four is 4472."
)


@pytest.mark.live
async def test_live_extractor_reads_the_brief_sentence() -> None:
    settings = Settings(fixtures_dir=SNAPSHOT_DIR, demo_today=date(2026, 3, 10))
    container = Container.build(settings)
    extractor = container.make_extractor(container.make_llm())  # server key from the environment or .env
    result = await extractor.extract(MARGARET, new_state())
    assert not result.degraded, "the live extractor call failed and fell back to regex"
    assert result.identity[IdentityField.FULL_NAME].raw.casefold() == "margaret chen"
    assert result.identity[IdentityField.DOB].raw == "1985-03-15"
    assert result.policy_number == "POL-9921"
    assert result.claim_status is ClaimStatus.DENIED
    assert result.date_hint is not None and result.date_hint.month == DateHint(month=1).month
    assert Path.DENIAL_QUESTION in {i.path for i in result.intents}
