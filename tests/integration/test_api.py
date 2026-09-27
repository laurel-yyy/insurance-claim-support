"""HTTP API with FakeLLMClient: the Margaret flow, keys, masking, errors and static files."""

import json
import logging
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from sop_agent.config import Settings
from sop_agent.container import LLMNotConfiguredError
from sop_agent.llm.base import LLMRequest, LLMResponse
from sop_agent.llm.fake import FakeLLMClient, ScriptItem
from sop_agent.main import create_app
from sop_agent.nlu.wire import NLUWireBase
from sop_agent.postprocess.summary import EmailContent
from tests.helpers import SNAPSHOT_DIR
from tests.integration.harness import SAFE_REPLY, faithful_summary
from tests.integration.test_end_to_end import MARGARET, MARGARET_NLU
from tests.nlu_helpers import wire_payload

SERVER_KEY = "sk-ant-server-test-key-000"
CLIENT_KEY = "sk-ant-client-test-key-111"
SECRETS = ("1985-03-15", "4472", "6505212836", "margaret@email.com", SERVER_KEY, CLIENT_KEY)


class ScriptedLLM:
    """Routes requests like the integration harness: scripted NLU, safe replies, faithful summaries."""

    def __init__(self) -> None:
        self.nlu: list[dict[str, Any]] = []
        self.keys: list[str | None] = []

    def factory(self, api_key: str | None) -> FakeLLMClient:
        self.keys.append(api_key)
        return FakeLLMClient(self.route)

    def route(self, request: LLMRequest) -> ScriptItem:
        model = request.output_model
        if model is not None and issubclass(model, NLUWireBase):
            parsed = model.model_validate(wire_payload(**(self.nlu.pop(0) if self.nlu else {})))
            return LLMResponse(text=parsed.model_dump_json(), parsed=parsed)
        if model is EmailContent:
            return faithful_summary(request)
        return LLMResponse(text=SAFE_REPLY)


def _settings(tmp_path: Path, **overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "_env_file": None,
        "fixtures_dir": SNAPSHOT_DIR,
        "demo_today": date(2026, 3, 10),
        "var_dir": tmp_path / "var",
        "anthropic_api_key": SecretStr(SERVER_KEY),
        "scenarios_dir": Path("evals/scenarios"),
    }
    return Settings(**{**base, **overrides})


@pytest.fixture
def llm() -> ScriptedLLM:
    return ScriptedLLM()


@pytest.fixture
def client(tmp_path: Path, llm: ScriptedLLM) -> Iterator[TestClient]:
    with TestClient(create_app(_settings(tmp_path), llm.factory)) as test_client:
        yield test_client


def _start(client: TestClient, **body: Any) -> dict[str, Any]:
    response = client.post("/api/sessions", json=body)
    assert response.status_code == 201, response.text
    data: dict[str, Any] = response.json()
    return data


def _say(client: TestClient, session_id: str, text: str) -> dict[str, Any]:
    response = client.post(f"/api/sessions/{session_id}/messages", json={"text": text})
    assert response.status_code == 200, response.text
    data: dict[str, Any] = response.json()
    return data


def test_margaret_flow_over_http(client: TestClient, llm: ScriptedLLM) -> None:
    session = _start(client)
    assert session["phase"] == "VERIFY_ID" and "verify your identity" in session["reply"]
    sid = session["session_id"]
    llm.nlu += [MARGARET_NLU, {"dialog_acts": ["done"]}, {"dialog_acts": ["confirm"], "confirmation": "yes"}]
    first = _say(client, sid, MARGARET)
    assert first["phase"] == "PROCESS_CASE" and first["phase_trail"] == ["RESOLVE_INTENT", "PROCESS_CASE"]
    assert first["debug"]["verification"]["verified"] is True
    offer = _say(client, sid, "That's all")
    assert offer["phase"] == "POST_PROCESS" and "Send the summary" in offer["quick_replies"]
    assert offer["debug"]["email_draft"]["generated_by"] == "llm"
    offered_debug = json.dumps(offer["debug"])
    assert "margaret@email.com" not in offered_debug and offer["debug"]["email_draft"]["to"].startswith("m•")
    done = _say(client, sid, "Yes please")
    assert done["phase"] == "ENDED" and done["ended"] is True
    [email] = client.get(f"/api/sessions/{sid}/outbox").json()
    assert email["to"].startswith("m\N{BULLET}") and "CL-2048" in email["text"]


def test_responses_debug_view_and_traces_never_leak(
    tmp_path: Path, client: TestClient, llm: ScriptedLLM
) -> None:
    sid = _start(client)["session_id"]
    llm.nlu.append(MARGARET_NLU)
    turn = _say(client, sid, MARGARET)
    debug = client.get(f"/api/sessions/{sid}").json()
    for payload in (json.dumps(turn["debug"]), json.dumps(debug), client.get("/api/health").text):
        for secret in SECRETS:
            assert secret not in payload
    assert debug["identity_checklist"] == {
        "full_name": "provided", "dob": "provided", "phone": "missing", "email": "missing", "id_last4": "provided"
    }  # fmt: skip
    trace_files = list((tmp_path / "var" / "traces").glob("*.jsonl"))
    assert trace_files
    for secret in SECRETS:
        assert secret not in trace_files[0].read_text(encoding="utf-8")


def test_health_reports_config_without_the_key(client: TestClient) -> None:
    body = client.get("/api/health").json()
    assert body["llm_configured"] is True and body["demo_today"] == "2026-03-10"
    assert (body["company_name"], body["agent_name"]) == ("Northwind Insurance", "Morgan")
    assert body["data_summary"]["consent_scenarios"] == ["default", "timeout"]


def test_unknown_or_expired_session_is_404(client: TestClient) -> None:
    responses = [
        client.post("/api/sessions/nope/messages", json={"text": "hi"}),
        client.get("/api/sessions/nope"),
        client.get("/api/sessions/nope/outbox"),
    ]
    for response in responses:
        assert response.status_code == 404 and "Start a new conversation" in response.json()["detail"]


def test_bad_requests_are_rejected(client: TestClient) -> None:
    assert client.post("/api/sessions", json={"consent_scenario": "made_up"}).status_code == 422
    sid = _start(client)["session_id"]
    assert client.post(f"/api/sessions/{sid}/messages", json={"text": ""}).status_code == 422
    assert client.post(f"/api/sessions/{sid}/messages", json={"text": "x" * 2001}).status_code == 422


def test_no_key_anywhere_is_503_with_setup_instructions(tmp_path: Path, llm: ScriptedLLM) -> None:
    app = create_app(_settings(tmp_path, anthropic_api_key=None, allow_client_api_key=False), llm.factory)
    with TestClient(app) as client:
        response = client.post("/api/sessions", json={})
        assert response.status_code == 503 and "ANTHROPIC_API_KEY" in response.json()["detail"]
        assert client.get("/api/health").json()["llm_configured"] is False


def test_client_key_is_used_for_that_session_only_and_never_echoed(
    tmp_path: Path, llm: ScriptedLLM, caplog: pytest.LogCaptureFixture
) -> None:
    app = create_app(_settings(tmp_path, anthropic_api_key=None, allow_client_api_key=True), llm.factory)
    caplog.set_level(logging.DEBUG)
    with TestClient(app) as client:
        assert client.post("/api/sessions", json={}).status_code == 503  # no key given
        session = client.post("/api/sessions", json={"api_key": CLIENT_KEY})
        assert session.status_code == 201 and CLIENT_KEY not in session.text
        turn = _say(client, session.json()["session_id"], "hello")
        assert CLIENT_KEY not in json.dumps(turn)
    assert llm.keys == [CLIENT_KEY]
    assert CLIENT_KEY not in caplog.text
    for path in (tmp_path / "var").rglob("*"):
        if path.is_file():
            assert CLIENT_KEY not in path.read_text(encoding="utf-8")


def test_server_key_wins_over_a_client_key(client: TestClient, llm: ScriptedLLM) -> None:
    sid = _start(client, api_key=CLIENT_KEY)["session_id"]
    _say(client, sid, "hello")
    assert llm.keys == [None]  # built once for the server key; the client key was ignored


def test_scenarios_and_static_ui_are_served(client: TestClient) -> None:
    scenarios = client.get("/api/scenarios").json()
    names = {s["name"] for s in scenarios}
    assert {"margaret_happy_path", "representative_consent_timeout"} <= names
    timeout = next(s for s in scenarios if s["name"] == "representative_consent_timeout")
    assert timeout["consent_scenario"] == "timeout" and len(timeout["turns"]) == 6
    page = client.get("/")
    assert page.status_code == 200 and '<script type="module" src="app.js">' in page.text
    for asset in ("/app.js", "/styles.css"):
        assert client.get(asset).status_code == 200


def test_evicted_session_drops_its_client_key(tmp_path: Path, llm: ScriptedLLM) -> None:
    settings = _settings(tmp_path, anthropic_api_key=None, allow_client_api_key=True, max_sessions=1)
    app = create_app(settings, llm.factory)
    with TestClient(app) as client:
        first = client.post("/api/sessions", json={"api_key": CLIENT_KEY}).json()["session_id"]
        client.post("/api/sessions", json={"api_key": CLIENT_KEY})  # the cap evicts the first session
        access = app.state.runtime.access
        with pytest.raises(LLMNotConfiguredError):
            access.agents_for(first)
        assert client.post(f"/api/sessions/{first}/messages", json={"text": "hi"}).status_code == 404
