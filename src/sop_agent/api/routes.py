"""HTTP routes."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request

from sop_agent.api.schemas import HealthResponse
from sop_agent.container import Container

router = APIRouter(prefix="/api")


def get_container(request: Request) -> Container:
    container: Container = request.app.state.container
    return container


@router.get("/health", response_model=HealthResponse)
def health(container: Annotated[Container, Depends(get_container)]) -> HealthResponse:
    return HealthResponse(
        status="ok",
        llm_configured=container.settings.llm_configured,
        demo_today=container.clock.today(),
        data_summary=container.data_summary,
    )
