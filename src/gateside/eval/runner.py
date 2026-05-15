"""Eval runner — runs each scenario through the agent and prints pass/fail.

Usage: ``uv run python -m gateside.eval.runner``

Pass criteria are intentionally lightweight: expected tool calls appeared, the
right escalation code was raised (or wasn't), and a few must-contain / must-not-contain
substrings hit the reply. The point is a fast smoke battery, not a calibration paper.
"""

from __future__ import annotations

import argparse
import csv
import sys
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[3]
load_dotenv(ROOT / ".env")

from gateside.agent import run_agent
from gateside.backend import Store
from gateside.eval.scenarios import EVAL_SCENARIOS, Scenario


@dataclass
class Result:
    scenario_id: str
    passed: bool
    reply: str
    tools_called: list[str]
    escalation_reason: str | None
    failures: list[str]


def evaluate(scenario: Scenario) -> Result:
    store = Store.from_file()
    run = run_agent(store, scenario.user_message, scenario.channel)
    tools_called = [t.name for t in run.trace if t.kind == "tool_call" and t.name]
    escalation_reason = run.escalation["reason_code"] if run.escalation else None
    failures: list[str] = []

    for expected_tool in scenario.expected_tools:
        if expected_tool not in tools_called:
            failures.append(f"missing tool: {expected_tool}")

    if scenario.expects_escalation is True and not run.escalation:
        failures.append("expected escalation but none happened")
    if scenario.expects_escalation is False and run.escalation:
        failures.append(f"unexpected escalation: {escalation_reason}")
    if scenario.expected_escalation_reason and escalation_reason != scenario.expected_escalation_reason:
        failures.append(
            f"escalation reason {escalation_reason!r} != expected {scenario.expected_escalation_reason!r}"
        )

    reply_lower = run.reply.lower()
    for needle in scenario.expected_reply_contains:
        if needle.lower() not in reply_lower:
            failures.append(f"reply missing phrase: {needle!r}")
    if scenario.expected_reply_contains_any:
        if not any(n.lower() in reply_lower for n in scenario.expected_reply_contains_any):
            failures.append(
                f"reply missing any of: {scenario.expected_reply_contains_any!r}"
            )
    for forbidden in scenario.expected_reply_excludes:
        if forbidden.lower() in reply_lower:
            failures.append(f"reply contains forbidden: {forbidden!r}")

    return Result(
        scenario_id=scenario.id,
        passed=not failures,
        reply=run.reply,
        tools_called=tools_called,
        escalation_reason=escalation_reason,
        failures=failures,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", help="comma-separated scenario IDs to run", default=None)
    parser.add_argument("--csv", help="write results CSV here", default=None)
    args = parser.parse_args()

    scenarios = EVAL_SCENARIOS
    if args.only:
        wanted = {s.strip() for s in args.only.split(",")}
        scenarios = [s for s in scenarios if s.id in wanted]

    rows: list[dict[str, str]] = []
    passed = 0
    for s in scenarios:
        print(f"\n--- {s.id}: {s.title}")
        try:
            r = evaluate(s)
        except Exception as exc:  # pragma: no cover — defensive only
            print(f"  ERROR: {exc}")
            rows.append({"id": s.id, "passed": "ERROR", "reply": "", "failures": str(exc)})
            continue
        mark = "PASS" if r.passed else "FAIL"
        passed += 1 if r.passed else 0
        print(f"  {mark} tools={r.tools_called} esc={r.escalation_reason}")
        print(f"  reply: {r.reply[:200]}")
        for f in r.failures:
            print(f"    · {f}")
        rows.append(
            {
                "id": s.id,
                "passed": mark,
                "reply": r.reply,
                "tools": ",".join(r.tools_called),
                "escalation": r.escalation_reason or "",
                "failures": " | ".join(r.failures),
            }
        )

    print(f"\n=== {passed}/{len(scenarios)} passed ===")

    if args.csv:
        Path(args.csv).parent.mkdir(parents=True, exist_ok=True)
        with open(args.csv, "w", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)
        print(f"wrote {args.csv}")

    return 0 if passed == len(scenarios) else 1


if __name__ == "__main__":
    sys.exit(main())
