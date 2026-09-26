# CLAUDE.md

Insurance claims support agent built on an SOP harness: a fixed workflow
(VERIFY_ID → RESOLVE_INTENT → PROCESS_CASE → POST_PROCESS) that still converses naturally.
The full design is in `docs/SPEC.md`. Read it before any work; this file only holds the rules
that apply to every session.

## Core idea

Code owns the workflow; the LLM owns the language. The LLM extracts structured meaning and writes
replies. Deterministic code decides phases, gates, allowed actions, and executes side effects.

## Workflow

- Work on one milestone at a time (SPEC §17). Plan first and wait for approval, then tests, then code.
- Run `make check` (ruff, mypy, pytest) before reporting. Never finish with failing checks.
- Record assumptions and deviations in `docs/DECISIONS.md` (context, decision, consequence).
- `apps/insurance_claims/fixtures/` is read-only starter data. Never edit it.
- Don't add dependencies without saying why. No agent frameworks (LangChain, LangGraph, etc.).
- Don't implement features that are not in the spec. When the spec is silent, choose the safest behavior
  and ask.

## Invariants (never violate; each has tests)

1. LLM output never directly changes phase or verification status and never executes side effects.
2. Before verification completes (including representative authorization and consent), no policyholder
   record data enters any LLM request. `agent/context.py` is the only place that assembles grounding.
3. Before verification, replies contain no record data the caller didn't say themselves (guard G1).
4. Tools never take `party_id` from the LLM; ownership is enforced in `ToolRegistry`. "Not found" and
   "not yours" return the same error.
5. The LLM has no write tools. Consent requests, summary emails and live-agent transfers run only through
   the policy and `ActionExecutor` after explicit confirmation stored in state.
6. The summary email is sent only after an explicit yes to the latest offer.
7. Phase transitions follow the whitelist in `sop/transitions.py`; never return to VERIFY_ID.
8. Every LLM failure path has a deterministic fallback reply that leaks nothing (fail closed).
9. PII is masked in logs, traces and debug views. API keys are never logged or returned to the client.
10. No hard-coded party IDs, case IDs or fixture text in business logic. Everything is data-driven.

## Layering

- `sop/`, `domain/`, `data/` must not import `llm/`, `agent/` or `api/`.
- `sop/` is pure and synchronous: no network, no LLM, no randomness, no `datetime.now()`; time comes from
  the injected `Clock`. External status (such as consent) is passed in as observations.
- `llm/` is business-agnostic.
- Wiring happens only in `container.py` (composition root). Pass dependencies through constructors.

## LLM API rules (see SPEC §10.2)

- Structured output via `output_config.format` (or `client.messages.parse`) and Pydantic validation.
- Never send `temperature`, `top_p` or `top_k`. Never prefill the assistant turn.
  Never force `tool_choice`; use the default `auto`.
- Send `effort` only when configured. Read response content blocks by type (thinking blocks may appear);
  in tool loops, append the assistant's content blocks back verbatim before the tool results.
- LLM-facing wire schemas: every field required, no `X | None` or unions, empty values mean "not
  mentioned". Convert to Optional domain models in code. Lowercase enum strings before validation.
- Never put customer data in JSON schemas (no case IDs or names in enums).

## Code style

- Python 3.12, full type hints, mypy clean. Pydantic v2 models for data that crosses module boundaries;
  domain models are frozen.
- `StrEnum` for phases, paths, events, fields and statuses; no magic strings.
- Small modules (under ~300 lines) and functions (under ~50 lines where practical).
- Docstrings on public classes and functions explain why, not what. Reference rule IDs (V2, K4, INV-3).
- Prompts live in `src/sop_agent/agent/prompts/*.md`, loaded by `prompt_loader.py`. No long prompt strings
  inline.
- Domain-specific exceptions; no bare `except`. No `print`; use the structured logger.

## Testing

- Unit tests for every rule in `sop/` and `domain/`, using `tests/fixtures/starter_snapshot/` plus
  `tests/fixtures/edge_cases/`, never the live app fixtures directory.
- LLM-dependent code is tested with `FakeLLMClient`. Live tests are marked `@pytest.mark.live` and skipped
  without an API key.
- Test names describe behavior, e.g. `test_v1_rejects_fields_matching_different_policyholders`.
- Never weaken an assertion to make a test pass. If a test is wrong, explain why before changing it.
- Never special-case scenario text in production code to pass an eval.

## Commands

```bash
make install      # uv sync
make dev          # run the app with reload on http://localhost:8000
make test         # unit + integration tests (no live LLM)
make check        # lint + typecheck + test
make live         # live LLM tests (needs ANTHROPIC_API_KEY)
make eval         # scenario evals, writes evals/report.md
make docker-build && make docker-run
```

## Language

All code, comments, prompts, commit messages and docs are in English, except `docs/SPEC.md`.

## Definition of done (per milestone)

- Acceptance criteria in SPEC §17.1 are met and `make check` passes.
- New behavior has tests, including at least one failure or edge case per rule.
- `docs/DECISIONS.md` is updated for any deviation.
- The report to the user lists what was built, test results, deviations and open questions.
