"""Test helpers for building repositories from the test fixture directories (never the app fixtures)."""

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

from sop_agent.data.loaders import (
    CLAIMS_FILE,
    CONSENT_FILE,
    POLICYHOLDERS_FILE,
    REPRESENTATIVES_FILE,
    RawFixtures,
    build_repository,
    read_raw_fixtures,
)
from sop_agent.data.repository import InMemoryRepository

FIXTURES_ROOT = Path(__file__).parent / "fixtures"
SNAPSHOT_DIR = FIXTURES_ROOT / "starter_snapshot"
EDGE_CASES_DIR = FIXTURES_ROOT / "edge_cases"


def _read_optional(path: Path, default: Any) -> Any:
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else default


def merged_raw_fixtures() -> RawFixtures:
    """Snapshot plus edge-case records, still unvalidated."""
    base = read_raw_fixtures(SNAPSHOT_DIR)
    return replace(
        base,
        policyholders=base.policyholders + _read_optional(EDGE_CASES_DIR / POLICYHOLDERS_FILE, []),
        claims=base.claims + _read_optional(EDGE_CASES_DIR / CLAIMS_FILE, []),
        representatives=base.representatives + _read_optional(EDGE_CASES_DIR / REPRESENTATIVES_FILE, []),
        consent_scenarios={**base.consent_scenarios, **_read_optional(EDGE_CASES_DIR / CONSENT_FILE, {})},
    )


def snapshot_repository() -> InMemoryRepository:
    return build_repository(read_raw_fixtures(SNAPSHOT_DIR))


def merged_repository() -> InMemoryRepository:
    """One repository with snapshot + edge cases, validated by the production loader."""
    return build_repository(merged_raw_fixtures())
