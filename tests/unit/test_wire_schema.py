"""The LLM-facing schemas stay inside the structured-output rules and limits."""

import json
from collections.abc import Iterator
from typing import Any

import pytest
from pydantic import BaseModel

from sop_agent.data.repository import InMemoryRepository
from sop_agent.llm.anthropic_client import strict_schema
from sop_agent.nlu.wire import SelectorWire, build_nlu_wire
from sop_agent.sop.guidance import topic_names
from tests.nlu_helpers import wire_payload as _valid_payload
from tests.policy_helpers import record_tokens

MAX_STRICT_TOOLS = 20
MAX_OPTIONAL_PARAMS = 24
MAX_UNION_PARAMS = 16
UNION_KEYS = ("anyOf", "oneOf", "allOf")


def _nodes(schema: Any) -> Iterator[dict[str, Any]]:
    if isinstance(schema, dict):
        yield schema
        for value in schema.values():
            yield from _nodes(value)
    elif isinstance(schema, list):
        for value in schema:
            yield from _nodes(value)


def _objects(schema: dict[str, Any]) -> list[dict[str, Any]]:
    return [n for n in _nodes(schema) if n.get("type") == "object"]


def _wire_schemas(repo: InMemoryRepository) -> list[tuple[str, dict[str, Any]]]:
    models: list[type[BaseModel]] = [build_nlu_wire(repo.document_guideline()), SelectorWire]
    return [(m.__name__, strict_schema(m)) for m in models]


@pytest.fixture(params=["snapshot", "merged"])
def repo(
    request: pytest.FixtureRequest, snapshot_repo: InMemoryRepository, merged_repo: InMemoryRepository
) -> InMemoryRepository:
    return snapshot_repo if request.param == "snapshot" else merged_repo


def test_schemas_have_no_unions_or_null_types(repo: InMemoryRepository) -> None:
    for name, schema in _wire_schemas(repo):
        for node in _nodes(schema):
            assert not any(k in node for k in UNION_KEYS), f"{name} has a union: {node}"
            kind = node.get("type")
            assert kind != "null" and not isinstance(kind, list), f"{name} has a null/multi type: {node}"


def test_every_object_is_closed_and_every_field_required(repo: InMemoryRepository) -> None:
    for name, schema in _wire_schemas(repo):
        for node in _objects(schema):
            assert node.get("additionalProperties") is False, name
            assert set(node.get("required", [])) == set(node.get("properties", {})), name


def test_schema_complexity_stays_within_api_limits(repo: InMemoryRepository) -> None:
    optional = unions = 0
    for _, schema in _wire_schemas(repo):
        for node in _objects(schema):
            props = node.get("properties", {})
            optional += len(set(props) - set(node.get("required", [])))
            unions += sum(
                1
                for p in props.values()
                if any(k in p for k in UNION_KEYS) or isinstance(p.get("type"), list)
            )
    assert optional <= MAX_OPTIONAL_PARAMS
    assert unions <= MAX_UNION_PARAMS
    assert (optional, unions) == (0, 0)  # the design goal is stricter than the limit


def test_strict_tool_limit_constant_matches_the_api_limit() -> None:
    assert MAX_STRICT_TOOLS == 20  # The API allows at most 20 strict tools per request


def test_enum_values_are_lowercase(repo: InMemoryRepository) -> None:
    for name, schema in _wire_schemas(repo):
        for node in _nodes(schema):
            for value in node.get("enum", []):
                assert value == value.casefold(), f"{name} enum value not lowercase: {value}"


def test_followup_topic_enum_is_generated_from_the_guideline(repo: InMemoryRepository) -> None:
    schema = strict_schema(build_nlu_wire(repo.document_guideline()))
    topic_enums = [n["enum"] for n in _nodes(schema) if n.get("title") == "FollowupTopicName"]
    assert topic_enums == [list(topic_names(repo.document_guideline()))]


def test_schemas_contain_no_customer_data(repo: InMemoryRepository) -> None:
    tokens = record_tokens(repo) | {h.name.casefold() for h in repo.policyholders()}
    for name, schema in _wire_schemas(repo):
        rendered = json.dumps(schema).casefold()
        leaked = sorted(t for t in tokens if t and t in rendered)
        assert not leaked, f"{name} schema contains record data: {leaked}"


def test_enum_strings_are_lowercased_before_validation(snapshot_repo: InMemoryRepository) -> None:
    wire = build_nlu_wire(snapshot_repo.document_guideline())
    parsed = wire.model_validate(
        _valid_payload(
            dialog_acts=["Provide_Identity", "CONFIRM"],
            scope="In_Scope",
            confirmation="YES",
            followup_topics=["Submission_Method"],
            intents=[{"path": "DENIAL_QUESTION", "confidence": 0.9}],
            questions=[{"text": "why", "kind": "ACCOUNT"}],
        )
    )
    assert [a.value for a in parsed.dialog_acts] == ["provide_identity", "confirm"]
    assert parsed.confirmation.value == "yes"
    assert parsed.topics() == ["submission_method"]
    assert parsed.intents[0].path.value == "denial_question"


def test_unknown_labels_degrade_per_field_instead_of_failing(snapshot_repo: InMemoryRepository) -> None:
    wire = build_nlu_wire(snapshot_repo.document_guideline())
    parsed = wire.model_validate(
        _valid_payload(
            dialog_acts=["provide_identity", "shrug"],
            followup_topics=["not_a_topic", "submission_method"],
            declined_fields=["favorite_color", "dob"],
            emotion="elated",
            confirmation="maybe",
            caller_role="boss",
            intents=[{"path": "buy_a_car", "confidence": 0.9}],
            questions=[{"text": "hmm", "kind": "weird"}],
        )
    )
    assert [a.value for a in parsed.dialog_acts] == ["provide_identity"]
    assert parsed.topics() == ["submission_method"]
    assert [f.value for f in parsed.declined_fields] == ["dob"]
    assert (parsed.emotion.value, parsed.confirmation.value, parsed.caller_role.value) == (
        "neutral",
        "unclear",
        "unknown",
    )
    assert parsed.intents[0].path.value == "none"
    assert parsed.questions[0].kind.value == "account"  # a missing or unknown kind is treated as account


def test_missing_required_field_still_fails_validation(snapshot_repo: InMemoryRepository) -> None:
    wire = build_nlu_wire(snapshot_repo.document_guideline())
    payload = _valid_payload()
    del payload["scope"]
    with pytest.raises(ValueError):
        wire.model_validate(payload)
