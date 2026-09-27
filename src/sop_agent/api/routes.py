"""HTTP routes (SPEC §13.1)."""

from dataclasses import dataclass
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status

from sop_agent.agent.orchestrator import Orchestrator, TurnResult
from sop_agent.api.debug import build_debug
from sop_agent.api.scenarios import load_scenarios
from sop_agent.api.schemas import (
    CreateSessionRequest,
    DebugView,
    EmailRecordOut,
    HealthResponse,
    MessageRequest,
    ScenarioInfo,
    TurnResponse,
)
from sop_agent.container import Container, LLMAccess, LLMNotConfiguredError
from sop_agent.memory.state import SessionState
from sop_agent.memory.store import SessionNotFoundError
from sop_agent.observability.masking import mask_email
from sop_agent.observability.trace import LatestTraceSink

NO_KEY_DETAIL = (
    "No API key is configured. Set ANTHROPIC_API_KEY on the server, or enter a key in the UI "
    "(requires ALLOW_CLIENT_API_KEY=true)."
)
SESSION_GONE_DETAIL = "This conversation has expired or doesn't exist. Start a new conversation."

router = APIRouter(prefix="/api")


@dataclass(frozen=True)
class Runtime:
    container: Container
    access: LLMAccess
    orchestrator: Orchestrator
    traces: LatestTraceSink


def get_runtime(request: Request) -> Runtime:
    runtime: Runtime = request.app.state.runtime
    return runtime


RuntimeDep = Annotated[Runtime, Depends(get_runtime)]


@router.get("/health", response_model=HealthResponse)
def health(rt: RuntimeDep) -> HealthResponse:
    s = rt.container.settings
    return HealthResponse(
        status="ok",
        llm_configured=s.llm_configured,
        allow_client_api_key=s.allow_client_api_key,
        demo_today=rt.container.clock.today(),
        company_name=s.company_name,
        agent_name=s.agent_name,
        data_summary=rt.container.data_summary,
    )


@router.post("/sessions", response_model=TurnResponse, status_code=status.HTTP_201_CREATED)
def create_session(body: CreateSessionRequest, rt: RuntimeDep) -> TurnResponse:
    key = body.api_key.get_secret_value().strip() if body.api_key else None
    if not rt.access.can_serve(key):
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, NO_KEY_DETAIL)
    scenario = body.consent_scenario
    if scenario and rt.container.repository.consent_scenario(scenario) is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Unknown consent scenario.")
    result = rt.orchestrator.start_session(scenario)
    rt.access.register(result.session_id, key)
    return _turn_response(rt, result)


@router.post("/sessions/{session_id}/messages", response_model=TurnResponse)
async def post_message(session_id: str, body: MessageRequest, rt: RuntimeDep) -> TurnResponse:
    _require_session(rt, session_id)
    try:
        result = await rt.orchestrator.handle_turn(session_id, body.text.strip())
    except SessionNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, SESSION_GONE_DETAIL) from exc
    except LLMNotConfiguredError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, NO_KEY_DETAIL) from exc
    return _turn_response(rt, result)


@router.get("/sessions/{session_id}", response_model=DebugView)
def get_session(session_id: str, rt: RuntimeDep) -> DebugView:
    state = _require_session(rt, session_id)
    return build_debug(state, rt.traces.latest(session_id))


@router.get("/sessions/{session_id}/outbox", response_model=list[EmailRecordOut])
def get_outbox(session_id: str, rt: RuntimeDep) -> list[EmailRecordOut]:
    _require_session(rt, session_id)
    return [
        EmailRecordOut(
            message_id=r.message_id,
            to=mask_email(r.to),
            subject=r.subject,
            text=r.text,
            html=r.html,
            sent_at=r.sent_at,
        )
        for r in rt.container.services.outbox.outbox(session_id)
    ]


@router.get("/scenarios", response_model=list[ScenarioInfo])
def get_scenarios(rt: RuntimeDep) -> list[ScenarioInfo]:
    return load_scenarios(rt.container.settings.scenarios_dir)


def _require_session(rt: Runtime, session_id: str) -> SessionState:
    try:
        return rt.container.services.store.get(session_id)
    except SessionNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, SESSION_GONE_DETAIL) from exc


def _turn_response(rt: Runtime, result: TurnResult) -> TurnResponse:
    state = rt.container.services.store.get(result.session_id)
    return TurnResponse(
        session_id=result.session_id,
        reply=result.reply,
        phase=result.phase.value,
        verify_stage=result.verify_stage.value if result.verify_stage else None,
        phase_trail=[p.value for p in result.phase_trail],
        quick_replies=result.quick_replies,
        ended=result.ended,
        escalated=result.escalated,
        debug=build_debug(state, rt.traces.latest(result.session_id)),
    )
