"""Scenario scripts for the UI's Play/Step. Read from SCENARIOS_DIR/*.yaml."""

from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from sop_agent.api.schemas import ScenarioInfo
from sop_agent.observability.logging import get_logger

DEFAULT_CONSENT = "default"

_log = get_logger(__name__)


def load_scenarios(directory: Path) -> list[ScenarioInfo]:
    """A malformed file is skipped with a warning rather than breaking the UI."""
    if not directory.is_dir():
        return []
    scenarios: list[ScenarioInfo] = []
    for path in sorted(directory.glob("*.yaml")):
        try:
            data: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
            scenarios.append(
                ScenarioInfo(
                    name=str(data["name"]),
                    description=str(data.get("description", "")),
                    consent_scenario=str(data.get("consent_scenario", DEFAULT_CONSENT)),
                    turns=[str(t["user"]) for t in data.get("turns", [])],
                )
            )
        except (OSError, yaml.YAMLError, KeyError, TypeError, ValidationError):
            _log.warning("skipping malformed scenario file", extra={"fields": {"file": path.name}})
    return scenarios
