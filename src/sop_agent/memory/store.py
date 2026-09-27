"""SessionStore: in-memory sessions with a per-session lock (SPEC §11.4).

Requests for the same session are serialized, so repeated clicks can't interleave two turns. TTL and a session
cap arrive with the API in M6 (D45).
"""

import asyncio
from typing import Protocol

from sop_agent.memory.state import SessionState


class SessionNotFoundError(KeyError):
    pass


class SessionStore(Protocol):
    def get(self, session_id: str) -> SessionState: ...

    def put(self, state: SessionState) -> None: ...

    def lock(self, session_id: str) -> asyncio.Lock: ...


class InMemorySessionStore:
    def __init__(self) -> None:
        self._sessions: dict[str, SessionState] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def get(self, session_id: str) -> SessionState:
        try:
            return self._sessions[session_id].model_copy(deep=True)
        except KeyError as exc:
            raise SessionNotFoundError(session_id) from exc

    def put(self, state: SessionState) -> None:
        self._sessions[state.session_id] = state.model_copy(deep=True)

    def lock(self, session_id: str) -> asyncio.Lock:
        return self._locks.setdefault(session_id, asyncio.Lock())
