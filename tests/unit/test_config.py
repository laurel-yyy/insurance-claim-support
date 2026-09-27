from datetime import date

import pytest

from sop_agent.config import Settings


def _clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in list(Settings.model_fields):
        monkeypatch.delenv(name.upper(), raising=False)


def test_defaults_match_env_example(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_env(monkeypatch)
    s = Settings(_env_file=None)
    assert s.demo_today == date(2026, 3, 10)
    assert s.verify_min_matches == 3
    assert s.verify_max_failed_attempts == 3
    assert s.off_topic_offer_live_agent_at == 3
    assert s.off_topic_hard_limit == 5
    assert s.persuasion_max == 3
    assert s.tool_loop_max_rounds == 3
    assert s.consent_scenario == "default"
    assert s.language == "en"
    assert s.agent_name == "Morgan"
    assert s.llm_configured is False


def test_empty_demo_today_means_real_date(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_env(monkeypatch)
    monkeypatch.setenv("DEMO_TODAY", "")
    assert Settings(_env_file=None).demo_today is None


def test_empty_api_key_is_not_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_env(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    assert Settings(_env_file=None).llm_configured is False


def test_api_key_is_secret_and_absent_from_repr(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_env(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-secret")
    s = Settings(_env_file=None)
    assert s.llm_configured is True
    assert "sk-ant-test-secret" not in repr(s)
    assert "sk-ant-test-secret" not in s.model_dump_json()


def test_api_key_whitespace_is_trimmed(monkeypatch: pytest.MonkeyPatch) -> None:
    """`docker run --env-file` passes `ANTHROPIC_API_KEY= sk-...` through verbatim (D64)."""
    _clear_env(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", " sk-ant-test-secret \t")
    settings = Settings(_env_file=None)
    assert settings.anthropic_api_key is not None
    assert settings.anthropic_api_key.get_secret_value() == "sk-ant-test-secret"
    monkeypatch.setenv("ANTHROPIC_API_KEY", "   ")
    assert Settings(_env_file=None).llm_configured is False
