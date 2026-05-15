"""One-shot smoke test against the real OpenRouter endpoint.

Run with: uv run python scripts/smoke.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from gateside.agent import run_agent
from gateside.backend import Store
from gateside.prompts import CHANNEL_CHAT


def main() -> None:
    if not os.environ.get("OPENROUTER_API_KEY"):
        print("OPENROUTER_API_KEY missing in .env", file=sys.stderr)
        sys.exit(1)

    store = Store.from_file()
    run = run_agent(
        store,
        "Hi — my QR code won't scan at the gate. The event starts in 15 minutes. Order TS-100003.",
        CHANNEL_CHAT,
    )
    print("\n=== AGENT REPLY ===")
    print(run.reply)
    print("\n=== TRACE ===")
    for step in run.trace:
        if step.kind == "tool_call":
            print(f"  → {step.name}({step.arguments})")
        elif step.kind == "tool_result":
            print(f"  ← {step.name}: {step.result}")
        elif step.kind == "final":
            print(f"  ✓ final reply ({len(step.content or '')} chars)")
        else:
            print(f"  · {step.kind}: {step.content}")
    if run.escalation:
        print(f"\n=== ESCALATION === {run.escalation}")


if __name__ == "__main__":
    main()
