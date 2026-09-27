"""The JSON log formatter redacts API keys everywhere, including tracebacks (INV-9)."""

import json
import logging
import sys

from sop_agent.observability.logging import JsonFormatter

KEY = "sk-ant-api03-ExampleOnly_abc-123"


def _record(
    msg: str, fields: dict[str, object] | None = None, exc: BaseException | None = None
) -> logging.LogRecord:
    exc_info = None
    if exc is not None:
        try:
            raise exc
        except type(exc):
            exc_info = sys.exc_info()
    record = logging.LogRecord("test", logging.ERROR, __file__, 1, msg, None, exc_info)
    if fields is not None:
        record.fields = fields
    return record


def test_keys_are_redacted_in_message_fields_and_tracebacks() -> None:
    header_error = ValueError(f"Illegal header value b' {KEY}'")
    line = JsonFormatter().format(_record(f"calling with {KEY}", {"auth": f"Bearer {KEY}"}, header_error))
    assert KEY not in line and "sk-ant" not in line
    payload = json.loads(line)
    assert payload["msg"] == "calling with [redacted]"
    assert payload["auth"] == "Bearer [redacted]"
    assert "Illegal header value" in payload["exc"] and "[redacted]" in payload["exc"]


def test_ordinary_lines_are_unchanged() -> None:
    payload = json.loads(JsonFormatter().format(_record("fixtures loaded", {"claims": 5})))
    assert (payload["msg"], payload["claims"]) == ("fixtures loaded", 5)
