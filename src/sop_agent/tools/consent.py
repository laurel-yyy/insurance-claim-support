"""ScenarioConsentService: simulated real-time policyholder consent (A5-A7).

Each request or poll consumes one status from the session's scenario; once the sequence is used up, polls keep
returning the last status with `exhausted=True`. A mock SMS record is written to var/sms/ (phone masked).
"""

import json
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from sop_agent.data.repository import InMemoryRepository
from sop_agent.observability.logging import get_logger
from sop_agent.observability.masking import mask_phone
from sop_agent.sop.directive import ConsentObservation

DEFAULT_SCENARIO = "default"

_log = get_logger(__name__)


class ConsentError(Exception):
    pass


@dataclass
class _Request:
    sequence: tuple[str, ...]
    cursor: int = 0


@dataclass
class ScenarioConsentService:
    repo: InMemoryRepository
    sms_dir: Path | None = None
    _requests: dict[str, _Request] = field(default_factory=dict)

    def request(self, party_id: str, scenario: str) -> tuple[str, ConsentObservation]:
        """Send the approval request; returns the request ID and the first status in the sequence."""
        found = self.repo.consent_scenario(scenario) or self.repo.consent_scenario(DEFAULT_SCENARIO)
        holder = self.repo.policyholder(party_id)
        if found is None or holder is None:
            raise ConsentError("consent scenario or policyholder unavailable")
        if found.name != scenario:
            _log.warning("unknown consent scenario; using default", extra={"fields": {"scenario": scenario}})
        request_id = f"CR-{uuid.uuid4().hex[:10]}"
        self._requests[request_id] = _Request(sequence=found.status_sequence)
        self._write_sms(request_id, holder.phone)
        return request_id, self._next(request_id)

    def poll(self, request_id: str) -> ConsentObservation:
        if request_id not in self._requests:
            raise ConsentError("unknown consent request")
        return self._next(request_id)

    def _next(self, request_id: str) -> ConsentObservation:
        entry = self._requests[request_id]
        index = min(entry.cursor, len(entry.sequence) - 1)
        entry.cursor += 1
        return ConsentObservation(status=entry.sequence[index], exhausted=entry.cursor >= len(entry.sequence))

    def _write_sms(self, request_id: str, phone: str) -> None:
        if self.sms_dir is None:
            return
        self.sms_dir.mkdir(parents=True, exist_ok=True)
        record = {
            "request_id": request_id,
            "to": mask_phone(phone),
            "text": "Please approve this call about your account.",
        }
        (self.sms_dir / f"{request_id}.json").write_text(json.dumps(record), encoding="utf-8")
