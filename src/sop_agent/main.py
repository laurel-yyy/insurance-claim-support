"""ASGI entry point: `uvicorn sop_agent.main:create_app --factory`."""

from fastapi import FastAPI

from sop_agent.api.routes import router
from sop_agent.config import Settings
from sop_agent.container import Container
from sop_agent.observability.logging import configure_logging


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the app; loading fixtures here makes bad data fail at startup, not mid-conversation."""
    resolved = settings or Settings()
    configure_logging(resolved.log_level)
    app = FastAPI(title="Insurance claims SOP agent")
    app.state.container = Container.build(resolved)
    app.include_router(router)
    return app
