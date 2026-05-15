"""The agent loop.

Hand-written tool-use loop calling OpenRouter's OpenAI-compatible chat completions
endpoint. No framework, no abstractions. Read top-to-bottom; that's the whole engine.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Callable

import httpx

from gateside.backend import Store
from gateside.prompts import build_system_prompt
from gateside.tools import TOOL_SCHEMAS, run_tool

DEFAULT_MODEL = os.environ.get("GATESIDE_MODEL", "anthropic/claude-sonnet-4.6")
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
MAX_STEPS = 8


def to_openai_tool_schemas(schemas: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert our Anthropic-shaped tool schemas to OpenAI's nested format."""
    return [
        {
            "type": "function",
            "function": {
                "name": s["name"],
                "description": s["description"],
                "parameters": s["input_schema"],
            },
        }
        for s in schemas
    ]


@dataclass
class TraceStep:
    """One observable step in the agent's run, for the UI trace panel."""

    kind: str  # "tool_call" | "tool_result" | "final" | "system"
    name: str | None = None
    arguments: dict[str, Any] | None = None
    result: dict[str, Any] | None = None
    content: str | None = None


@dataclass
class AgentRun:
    reply: str
    trace: list[TraceStep] = field(default_factory=list)
    messages: list[dict[str, Any]] = field(default_factory=list)
    escalation: dict[str, Any] | None = None


LLMCaller = Callable[[list[dict[str, Any]], list[dict[str, Any]], str], dict[str, Any]]


def call_openrouter(
    messages: list[dict[str, Any]], tools: list[dict[str, Any]], model: str
) -> dict[str, Any]:
    """Default LLM caller. Pure I/O — replaceable in tests."""
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY is not set")
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/balazsthomay/gateside",
        "X-Title": "Gateside - TicketSwap gate-failure agent",
    }
    payload = {
        "model": model,
        "messages": messages,
        "tools": tools,
        "tool_choice": "auto",
        "temperature": 0.2,
    }
    with httpx.Client(timeout=60.0) as client:
        resp = client.post(OPENROUTER_URL, json=payload, headers=headers)
        resp.raise_for_status()
        return resp.json()


def run_agent(
    store: Store,
    user_message: str,
    channel: str,
    history: list[dict[str, Any]] | None = None,
    *,
    model: str = DEFAULT_MODEL,
    llm: LLMCaller | None = None,
    max_steps: int = MAX_STEPS,
) -> AgentRun:
    """Run the agent end-to-end on a single user turn.

    The function is synchronous on purpose — the agent's reply is what the UI
    waits for. Streaming is nice but adds surface area we don't need for a
    portfolio demo.
    """
    if llm is None:
        llm = call_openrouter
    system = build_system_prompt(channel, store.now)
    messages: list[dict[str, Any]] = [{"role": "system", "content": system}]
    if history:
        messages.extend(history)
    messages.append({"role": "user", "content": user_message})

    trace: list[TraceStep] = []
    tools_openai = to_openai_tool_schemas(TOOL_SCHEMAS)
    escalation: dict[str, Any] | None = None

    for _ in range(max_steps):
        response = llm(messages, tools_openai, model)
        choice = response["choices"][0]
        msg = choice["message"]
        messages.append(_strip_assistant_message(msg))

        tool_calls = msg.get("tool_calls") or []
        content = msg.get("content")

        if not tool_calls:
            trace.append(TraceStep(kind="final", content=content or ""))
            return AgentRun(reply=content or "", trace=trace, messages=messages, escalation=escalation)

        for tc in tool_calls:
            name = tc["function"]["name"]
            raw_args = tc["function"]["arguments"] or "{}"
            try:
                args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
            except json.JSONDecodeError:
                args = {}
            trace.append(TraceStep(kind="tool_call", name=name, arguments=args))
            result = run_tool(store, name, args)
            trace.append(TraceStep(kind="tool_result", name=name, result=result))
            if name == "escalate_to_human" and result.get("escalated"):
                escalation = result
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": json.dumps(result),
                }
            )

    trace.append(
        TraceStep(
            kind="system",
            content="Hit max steps without a final reply — falling back to an escalation.",
        )
    )
    fallback = run_tool(
        store,
        "escalate_to_human",
        {
            "reason": "policy_ambiguous",
            "summary": "Agent did not converge within step budget; needs human review.",
        },
    )
    return AgentRun(
        reply="I'm handing this off to a teammate so it gets the attention it needs.",
        trace=trace,
        messages=messages,
        escalation=fallback,
    )


def _strip_assistant_message(msg: dict[str, Any]) -> dict[str, Any]:
    """Keep only the keys OpenRouter accepts on the next request."""
    out: dict[str, Any] = {"role": "assistant", "content": msg.get("content")}
    if msg.get("tool_calls"):
        out["tool_calls"] = msg["tool_calls"]
    return out
