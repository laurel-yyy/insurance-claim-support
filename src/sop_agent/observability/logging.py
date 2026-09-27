"""Structured JSON logging. Pass fields via `extra={"fields": {...}}`; values must already be masked.

As a last line of defense (INV-9), every formatted line has anything shaped like an API key redacted, including
exception tracebacks, where third-party error messages can quote request headers (D64).
"""

import json
import logging
from typing import Any

from sop_agent.observability.masking import redact_secrets

_RESERVED = "fields"


class JsonFormatter(logging.Formatter):
    """One JSON object per line so logs are machine-readable."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        fields = getattr(record, _RESERVED, None)
        if isinstance(fields, dict):
            payload.update(fields)
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return redact_secrets(json.dumps(payload, default=str))


def configure_logging(level: str) -> None:
    """Install the JSON formatter on the root logger (idempotent)."""
    root = logging.getLogger()
    root.setLevel(level.upper())
    if not any(isinstance(h.formatter, JsonFormatter) for h in root.handlers):
        handler = logging.StreamHandler()
        handler.setFormatter(JsonFormatter())
        root.addHandler(handler)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
