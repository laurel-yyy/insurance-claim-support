import pytest

from sop_agent.config import Settings
from sop_agent.data.repository import InMemoryRepository
from tests.helpers import merged_repository, snapshot_repository


@pytest.fixture
def snapshot_repo() -> InMemoryRepository:
    return snapshot_repository()


@pytest.fixture
def merged_repo() -> InMemoryRepository:
    return merged_repository()


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if Settings().llm_configured:  # ANTHROPIC_API_KEY from the environment or .env
        return
    skip_live = pytest.mark.skip(reason="ANTHROPIC_API_KEY not set")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip_live)
