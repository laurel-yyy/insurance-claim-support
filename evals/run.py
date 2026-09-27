"""Scenario eval runner: `python -m evals.run [--repeat N] [--scenario NAME]`.

Runs each scenario in-process through the real orchestrator with the real models, checks the assertions after
every turn, and writes evals/report.md. A cost ceiling stops the run before it spends more than `--max-cost`.
"""

import argparse
import asyncio
import json
import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from evals.assertions import TurnObservation, check_turn
from sop_agent.config import Settings
from sop_agent.container import Container
from sop_agent.llm.base import LLMClient, LLMRequest, LLMResponse
from sop_agent.nlu.wire import NLUWireBase, SelectorWire
from sop_agent.observability.logging import configure_logging, get_logger
from sop_agent.observability.trace import JsonlTraceWriter
from sop_agent.postprocess.summary import EmailContent

SCENARIOS_DIR = Path("evals/scenarios")
REPORT = Path("evals/report.md")
VAR_DIR = Path("var/evals")
# USD per million tokens (input, output), for the cost ceiling only.
PRICES: dict[str, tuple[float, float]] = {"haiku": (1.0, 5.0), "sonnet": (2.0, 10.0)}
DEFAULT_PRICE = (5.0, 25.0)

_log = get_logger("evals")


@dataclass
class Usage:
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost: float = 0.0
    by_component: dict[str, int] = field(default_factory=dict)

    def add(self, other: "Usage") -> None:
        self.calls += other.calls
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.cost += other.cost
        for key, value in other.by_component.items():
            self.by_component[key] = self.by_component.get(key, 0) + value


def _component(request: LLMRequest) -> str:
    model = request.output_model
    if model is not None and issubclass(model, NLUWireBase):
        return "extractor"
    if model is SelectorWire:
        return "selector"
    if model is EmailContent:
        return "summary"
    return "responder"


class CountingClient:
    """Wraps the real client to count calls, tokens and an estimated cost. Production code is unchanged."""

    def __init__(self, inner: LLMClient, usage: Usage, budget: "Budget") -> None:
        self._inner, self.usage, self._budget = inner, usage, budget

    async def create(self, request: LLMRequest) -> LLMResponse:
        self._budget.check()
        response = await self._inner.create(request)
        price_in, price_out = next((p for k, p in PRICES.items() if k in request.model), DEFAULT_PRICE)
        cost = (response.usage.input_tokens * price_in + response.usage.output_tokens * price_out) / 1_000_000
        self.usage.calls += 1
        self.usage.input_tokens += response.usage.input_tokens
        self.usage.output_tokens += response.usage.output_tokens
        self.usage.cost += cost
        component = _component(request)
        self.usage.by_component[component] = self.usage.by_component.get(component, 0) + 1
        self._budget.spent += cost
        return response


class BudgetExceededError(RuntimeError):
    pass


@dataclass
class Budget:
    limit: float
    spent: float = 0.0

    def check(self) -> None:
        if self.spent >= self.limit:
            raise BudgetExceededError(f"cost ceiling ${self.limit:.2f} reached")


@dataclass
class RunResult:
    scenario: str
    passed: bool
    failures: list[str]
    turns: int
    latency_ms: int
    usage: Usage


def load_scenario(path: Path) -> dict[str, Any]:
    data: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data


async def run_scenario(spec: dict[str, Any], budget: Budget, var_dir: Path) -> RunResult:
    today = spec.get("today")
    settings = Settings(
        demo_today=date.fromisoformat(str(today)) if today else date(2026, 3, 10),
        consent_scenario=str(spec.get("consent_scenario", "default")),
        var_dir=var_dir,
    )
    container = Container.build(settings)
    usage = Usage()
    llm = CountingClient(container.make_llm(), usage, budget)
    orchestrator = container.make_orchestrator(
        container.make_agents(llm), tracer=JsonlTraceWriter(var_dir / "traces")
    )
    session = orchestrator.start_session(settings.consent_scenario)
    store, outbox, index = container.services.store, container.services.outbox, container.services.index
    failures: list[str] = []
    caller_texts: list[str] = []
    started = time.perf_counter()
    turns = spec.get("turns", [])
    for number, turn in enumerate(turns, start=1):
        text = str(turn["user"])
        caller_texts.append(text)
        result = await orchestrator.handle_turn(session.session_id, text)
        obs = TurnObservation(
            result=result,
            state=store.get(session.session_id),
            caller_texts=list(caller_texts),
            emails_sent=len(outbox.outbox(session.session_id)),
            index=index,
        )
        expect = {"no_record_leak": True, **(turn.get("expect") or {})}
        failures += [f"turn {number}: {f}" for f in check_turn(obs, expect)]
        _log.info(
            "turn", extra={"fields": {"scenario": spec["name"], "turn": number, "phase": result.phase.value}}
        )
    latency = int((time.perf_counter() - started) * 1000 / max(len(turns), 1))
    return RunResult(spec["name"], not failures, failures, len(turns), latency, usage)


async def run_all(paths: list[Path], repeat: int, budget: Budget) -> list[RunResult]:
    """Scenarios run one after another (rate limits). Hitting the cost ceiling stops the run; the report is
    still written with what finished."""
    results: list[RunResult] = []
    for path in paths:
        spec = load_scenario(path)
        for attempt in range(repeat):
            try:
                results.append(await run_scenario(spec, budget, VAR_DIR / f"{spec['name']}-{attempt + 1}"))
            except BudgetExceededError as exc:
                results.append(RunResult(spec["name"], False, [f"stopped: {exc}"], 0, 0, Usage()))
                return results
            except Exception as exc:  # noqa: BLE001 - one broken scenario must not stop the report
                results.append(
                    RunResult(spec["name"], False, [f"error: {type(exc).__name__}: {exc}"], 0, 0, Usage())
                )
    return results


def write_report(results: list[RunResult], path: Path, repeat: int) -> str:
    by_name: dict[str, list[RunResult]] = {}
    for result in results:
        by_name.setdefault(result.scenario, []).append(result)
    total = Usage()
    lines = [
        "# Scenario eval report",
        "",
        f"Runs per scenario: {repeat}. Models: extractor and selector on Haiku 4.5, responder and summary on Sonnet 5.",
        "",
        "| Scenario | Passed | Pass rate | Avg latency per turn | Calls | Input tokens | Output tokens | Failed assertions |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for name, runs in by_name.items():
        passed = sum(r.passed for r in runs)
        usage = Usage()
        for r in runs:
            usage.add(r.usage)
            total.add(r.usage)
        latency = sum(r.latency_ms for r in runs) // len(runs)
        failed = "<br>".join(f for r in runs for f in r.failures) or ""
        lines.append(
            f"| {name} | {passed}/{len(runs)} | {passed / len(runs):.0%} | {latency / 1000:.1f} s | {usage.calls} "
            f"| {usage.input_tokens:,} | {usage.output_tokens:,} | {failed} |"
        )
    passed_all = sum(r.passed for r in results)
    lines += [
        "",
        f"**Total:** {passed_all}/{len(results)} runs passed. {total.calls} LLM calls, {total.input_tokens:,} input and "
        f"{total.output_tokens:,} output tokens, estimated cost ${total.cost:.2f}.",
        "",
        f"Calls by component: {json.dumps(dict(sorted(total.by_component.items())))}.",
    ]
    text = "\n".join(lines) + "\n"
    path.write_text(text, encoding="utf-8")
    return text


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--scenario", action="append", help="run only these scenarios (repeatable)")
    parser.add_argument("--report", type=Path, default=REPORT)
    parser.add_argument(
        "--max-cost", type=float, default=8.0, help="stop before spending more than this (USD)"
    )
    args = parser.parse_args()
    configure_logging("WARNING")
    paths = sorted(SCENARIOS_DIR.glob("*.yaml"))
    if args.scenario:
        paths = [p for p in paths if p.stem in set(args.scenario)]
    budget = Budget(limit=args.max_cost)
    results = asyncio.run(run_all(paths, args.repeat, budget))
    report = write_report(results, args.report, args.repeat)
    _log.warning(
        "eval finished", extra={"fields": {"passed": sum(r.passed for r in results), "runs": len(results)}}
    )
    for line in report.splitlines():
        if line.startswith("|") or line.startswith("**"):
            _log.warning(line)


if __name__ == "__main__":
    main()
