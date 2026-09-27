"""ASGI entry point: `uvicorn sop_agent.main:create_app --factory`."""

from collections.abc import Callable
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from sop_agent.api.routes import Runtime, router
from sop_agent.config import Settings
from sop_agent.container import Container, LLMAccess
from sop_agent.llm.base import LLMClient
from sop_agent.observability.logging import configure_logging
from sop_agent.observability.trace import JsonlTraceWriter, LatestTraceSink

STATIC_DIR = Path(__file__).parent / "web" / "static"


def create_app(
    settings: Settings | None = None, llm_factory: Callable[[str | None], LLMClient] | None = None
) -> FastAPI:
    """Build the app; loading fixtures here makes bad data fail at startup, not mid-conversation.

    `llm_factory(api_key)` builds an LLM client (None = the server key); tests inject a fake one.
    """
    resolved = settings or Settings()
    configure_logging(resolved.log_level)
    container = Container.build(resolved)
    access = LLMAccess(container, llm_factory or container.make_llm)
    traces = LatestTraceSink(JsonlTraceWriter(resolved.var_dir / "traces"))
    orchestrator = container.make_orchestrator(access.agents_for, tracer=traces)

    def forget(session_id: str) -> None:
        access.forget(session_id)
        traces.forget(session_id)

    container.services.store.on_evict = forget
    app = FastAPI(title="Insurance claims SOP agent")
    app.state.container = container
    app.state.runtime = Runtime(container=container, access=access, orchestrator=orchestrator, traces=traces)
    app.include_router(router)
    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="ui")
    return app
