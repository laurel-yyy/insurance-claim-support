"""EmailService protocol and MockOutbox: emails are written to var/outbox/ and kept in memory for the UI."""

import json
import uuid
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel


class EmailRecord(BaseModel):
    message_id: str
    session_id: str
    to: str
    subject: str
    text: str
    html: str = ""
    sent_at: datetime


class EmailService(Protocol):
    def send(
        self, session_id: str, to: str, subject: str, text: str, html: str, sent_at: datetime
    ) -> EmailRecord: ...

    def outbox(self, session_id: str) -> list[EmailRecord]: ...


class MockOutbox:
    def __init__(self, directory: Path | None = None) -> None:
        self._directory = directory
        self._sent: dict[str, list[EmailRecord]] = defaultdict(list)

    def send(
        self, session_id: str, to: str, subject: str, text: str, html: str, sent_at: datetime
    ) -> EmailRecord:
        record = EmailRecord(
            message_id=f"EM-{uuid.uuid4().hex[:10]}",
            session_id=session_id,
            to=to,
            subject=subject,
            text=text,
            html=html,
            sent_at=sent_at,
        )
        if self._directory is not None:
            self._directory.mkdir(parents=True, exist_ok=True)
            path = self._directory / f"{record.message_id}.json"
            path.write_text(json.dumps(record.model_dump(mode="json"), ensure_ascii=False), encoding="utf-8")
        self._sent[session_id].append(record)
        return record

    def outbox(self, session_id: str) -> list[EmailRecord]:
        return list(self._sent.get(session_id, []))
