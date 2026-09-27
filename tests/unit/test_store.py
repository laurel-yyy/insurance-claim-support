"""SessionStore: TTL, cap and eviction order, eviction hooks, per-session locks."""

import asyncio

import pytest

from sop_agent.memory.state import SessionState
from sop_agent.memory.store import InMemorySessionStore, SessionNotFoundError
from tests.builders import new_state


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def _state(session_id: str) -> SessionState:
    state = new_state()
    state.session_id = session_id
    return state


def test_get_returns_a_copy() -> None:
    store = InMemorySessionStore()
    store.put(_state("a"))
    copy = store.get("a")
    copy.counters.turn_index = 99
    assert store.get("a").counters.turn_index == 0


def test_sessions_expire_after_ttl_of_inactivity() -> None:
    clock, evicted = Clock(), list[str]()
    store = InMemorySessionStore(ttl_seconds=60, now=clock, on_evict=evicted.append)
    store.put(_state("a"))
    clock.now = 50
    store.get("a")  # activity renews the TTL
    clock.now = 100
    store.get("a")
    clock.now = 161
    with pytest.raises(SessionNotFoundError):
        store.get("a")
    assert evicted == ["a"] and not store.exists("a")


def test_cap_evicts_expired_first_then_least_recently_used() -> None:
    clock, evicted = Clock(), list[str]()
    store = InMemorySessionStore(ttl_seconds=60, max_sessions=3, now=clock, on_evict=evicted.append)
    for sid in ("a", "b", "c"):
        store.put(_state(sid))
        clock.now += 1
    store.get("a")  # b is now the least recently used
    store.put(_state("d"))
    assert evicted == ["b"] and len(store) == 3
    clock.now = 200  # everything expired
    store.put(_state("e"))
    assert set(evicted) == {"b", "a", "c", "d"} and len(store) == 1


def test_unknown_session_raises() -> None:
    with pytest.raises(SessionNotFoundError):
        InMemorySessionStore().get("missing")


async def test_lock_serializes_turns_for_one_session() -> None:
    store = InMemorySessionStore()
    order: list[str] = []

    async def turn(name: str) -> None:
        async with store.lock("s"):
            order.append(f"{name} start")
            await asyncio.sleep(0.01)
            order.append(f"{name} end")

    await asyncio.gather(turn("one"), turn("two"))
    assert order == ["one start", "one end", "two start", "two end"]
    assert store.lock("s") is store.lock("s") and store.lock("s") is not store.lock("t")
