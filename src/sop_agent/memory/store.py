"""SessionStore: in-memory sessions with a per-session lock, TTL and a session cap (SPEC §11.4, §13.1).

Requests for one session are serialized. Sessions expire after `ttl_seconds` without activity (monotonic clock,
not the demo date). At the cap, expired sessions are evicted first, then the least recently used.
"""

import asyncio
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from sop_agent.memory.state import SessionState


class SessionNotFoundError(KeyError):
    """Unknown or expired session; the API answers 404 and the UI offers a new conversation."""


class SessionStore(Protocol):
    def get(self, session_id: str) -> SessionState: ...

    def put(self, state: SessionState) -> None: ...

    def lock(self, session_id: str) -> asyncio.Lock: ...


@dataclass
class _Entry:
    state: SessionState
    touched: float


class InMemorySessionStore:
    def __init__(
        self,
        ttl_seconds: float = 120 * 60,
        max_sessions: int = 500,
        *,
        now: Callable[[], float] = time.monotonic,
        on_evict: Callable[[str], None] | None = None,
    ) -> None:
        self._ttl = ttl_seconds
        self._max = max(1, max_sessions)
        self._now = now
        self.on_evict = on_evict  # Wired by the composition root (per-session keys, debug traces)
        self._entries: OrderedDict[str, _Entry] = OrderedDict()
        self._locks: dict[str, asyncio.Lock] = {}

    def __len__(self) -> int:
        return len(self._entries)

    def get(self, session_id: str) -> SessionState:
        entry = self._entries.get(session_id)
        if entry is None or self._expired(entry):
            if entry is not None:
                self._evict(session_id)
            raise SessionNotFoundError(session_id)
        self._touch(session_id, entry)
        return entry.state.model_copy(deep=True)

    def put(self, state: SessionState) -> None:
        session_id = state.session_id
        if session_id not in self._entries:
            self._make_room()
        self._entries[session_id] = _Entry(state=state.model_copy(deep=True), touched=self._now())
        self._entries.move_to_end(session_id)

    def lock(self, session_id: str) -> asyncio.Lock:
        return self._locks.setdefault(session_id, asyncio.Lock())

    def exists(self, session_id: str) -> bool:
        entry = self._entries.get(session_id)
        return entry is not None and not self._expired(entry)

    def _expired(self, entry: _Entry) -> bool:
        return self._now() - entry.touched > self._ttl

    def _touch(self, session_id: str, entry: _Entry) -> None:
        entry.touched = self._now()
        self._entries.move_to_end(session_id)

    def _make_room(self) -> None:
        for session_id in [sid for sid, e in self._entries.items() if self._expired(e)]:
            self._evict(session_id)
        while len(self._entries) >= self._max:
            oldest = next(iter(self._entries))
            self._evict(oldest)

    def _evict(self, session_id: str) -> None:
        self._entries.pop(session_id, None)
        lock = self._locks.get(session_id)
        if lock is not None and not lock.locked():
            del self._locks[session_id]
        if self.on_evict is not None:
            self.on_evict(session_id)
