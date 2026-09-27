"""Application settings (SPEC §14.1), read from the environment and an optional .env file."""

from datetime import date
from pathlib import Path

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All runtime configuration. The API key is a SecretStr so it never appears in logs or reprs (INV-9)."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Model access
    anthropic_api_key: SecretStr | None = None
    allow_client_api_key: bool = True
    llm_provider: str = "anthropic"
    extractor_model: str = "claude-haiku-4-5-20251001"
    responder_model: str = "claude-sonnet-5"
    responder_effort: str | None = "low"
    llm_timeout_seconds: float = 30.0

    # Data and demo clock
    fixtures_dir: Path = Path("apps/insurance_claims/fixtures")
    faq_path: Path = Path("data/kb/faq.md")
    language: str = "en"
    demo_today: date | None = date(2026, 3, 10)
    consent_scenario: str = "default"

    # SOP thresholds
    verify_min_matches: int = 3
    verify_max_failed_attempts: int = 3
    off_topic_offer_live_agent_at: int = 3
    off_topic_hard_limit: int = 5
    persuasion_max: int = 3
    tool_loop_max_rounds: int = 3

    # Branding and runtime
    company_name: str = "Northwind Insurance"
    agent_name: str = "Morgan"
    session_ttl_minutes: int = 120
    max_sessions: int = 500
    log_level: str = "INFO"
    scenarios_dir: Path = Path("evals/scenarios")  # Demo and eval scripts listed by GET /api/scenarios
    var_dir: Path = Path("var")  # Runtime artifacts: outbox/, sms/, traces/, handoffs/ (gitignored)

    @field_validator("anthropic_api_key", "responder_effort", "demo_today", mode="before")
    @classmethod
    def _empty_means_unset(cls, value: object) -> object:
        """Trim whitespace, and treat an empty value as "not set": DEMO_TODAY= selects the real date (§14.1).

        Trimming matters for `docker run --env-file`, which passes `KEY= value` through verbatim.
        """
        if isinstance(value, str):
            value = value.strip()
            return value or None
        return value

    @property
    def llm_configured(self) -> bool:
        """Whether a server-side API key is available."""
        return self.anthropic_api_key is not None and bool(self.anthropic_api_key.get_secret_value())
