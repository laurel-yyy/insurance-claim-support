from datetime import date
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from sop_agent.config import Settings
from sop_agent.domain.errors import DataLoadError
from sop_agent.main import create_app
from tests.helpers import SNAPSHOT_DIR


def _client(**overrides: Any) -> TestClient:
    settings = Settings(_env_file=None, fixtures_dir=SNAPSHOT_DIR, demo_today=date(2026, 3, 10))
    return TestClient(create_app(settings.model_copy(update=overrides)))


def test_health_returns_data_summary_and_demo_date() -> None:
    body = _client(anthropic_api_key=None).get("/api/health").json()
    assert body["status"] == "ok"
    assert body["demo_today"] == "2026-03-10"
    assert body["llm_configured"] is False
    assert body["data_summary"] == {
        "policyholders": 4,
        "claims": 5,
        "representatives": 1,
        "consent_scenarios": ["default", "timeout"],
        "guidance_topics": 6,
    }


def test_health_reports_key_presence_without_returning_it() -> None:
    response = _client(anthropic_api_key=SecretStr("sk-ant-never-leak")).get("/api/health")
    assert response.json()["llm_configured"] is True
    assert "sk-ant-never-leak" not in response.text


def test_app_fails_at_startup_when_fixtures_are_missing(tmp_path: Path) -> None:
    with pytest.raises(DataLoadError):
        _client(fixtures_dir=tmp_path)
