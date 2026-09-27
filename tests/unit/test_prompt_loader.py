import pytest

from sop_agent.agent.prompt_loader import MissingPlaceholderError, PromptNotFoundError, load_prompt, render

EXTRACTOR_PLACEHOLDERS = (
    "today",
    "phase",
    "pending_question",
    "last_agent_message",
    "followup_topics_with_descriptions",
)


def test_prompts_load() -> None:
    assert load_prompt("extractor.md").startswith("You are the language-understanding step")
    assert "CANDIDATES" in load_prompt("selector.md")


def test_missing_prompt_fails_clearly() -> None:
    with pytest.raises(PromptNotFoundError, match=r"nope\.md"):
        load_prompt("nope.md")


def test_render_fills_only_named_placeholders_and_keeps_json_braces() -> None:
    template = 'TODAY: {today}\n{"dialog_acts":["confirm"]} and {text, kind} {other}'
    assert (
        render(template, today="2026-03-10")
        == 'TODAY: 2026-03-10\n{"dialog_acts":["confirm"]} and {text, kind} {other}'
    )


def test_render_is_single_pass() -> None:
    assert render("{a} {b}", a="{b}", b="x") == "{b} x"


def test_render_rejects_unknown_placeholders() -> None:
    with pytest.raises(MissingPlaceholderError):
        render("{today}", today="x", phase="y")


def test_extractor_prompt_has_every_placeholder_the_extractor_fills() -> None:
    text = load_prompt("extractor.md")
    for name in EXTRACTOR_PLACEHOLDERS:
        assert "{" + name + "}" in text


def test_prompt_examples_use_no_starter_record_data() -> None:
    """INV-2: the few-shot examples go into every request, including pre-verification ones."""
    text = load_prompt("extractor.md").casefold()
    for token in (
        "margaret",
        "pol-9921",
        "1985-03-15",
        "4472",
        "margaret@email.com",
        "david chen",
        "650-521-2836",
        "cl-2048",
    ):
        assert token not in text
