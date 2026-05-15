"""Tests for the agent loop using a stub LLM. No network calls."""

from __future__ import annotations

import json
from typing import Any

import pytest

from gateside.agent import run_agent, to_openai_tool_schemas
from gateside.backend import Store
from gateside.prompts import CHANNEL_CHAT, CHANNEL_EMAIL, build_system_prompt
from gateside.tools import TOOL_SCHEMAS


@pytest.fixture
def store() -> Store:
    return Store.from_file()


def _assistant(content: str | None = None, tool_calls: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    msg: dict[str, Any] = {"role": "assistant", "content": content}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return {"choices": [{"message": msg}]}


def _tc(call_id: str, name: str, args: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(args)},
    }


class StubLLM:
    """Replays a scripted list of responses; records what messages it saw."""

    def __init__(self, responses: list[dict[str, Any]]):
        self.responses = list(responses)
        self.seen: list[list[dict[str, Any]]] = []

    def __call__(self, messages, tools, model):
        self.seen.append([dict(m) for m in messages])
        return self.responses.pop(0)


# Prompt construction --------------------------------------------------------

def test_build_system_prompt_chat_has_chat_rules(store: Store) -> None:
    p = build_system_prompt(CHANNEL_CHAT, store.now)
    assert "Channel: CHAT" in p
    assert "Plain text only" in p


def test_build_system_prompt_email_has_email_rules(store: Store) -> None:
    p = build_system_prompt(CHANNEL_EMAIL, store.now)
    assert "Channel: EMAIL" in p
    assert "Subject:" in p


def test_build_system_prompt_unknown_channel_raises(store: Store) -> None:
    with pytest.raises(ValueError):
        build_system_prompt("voice", store.now)


def test_tool_schemas_translate_to_openai_shape() -> None:
    converted = to_openai_tool_schemas(TOOL_SCHEMAS)
    assert all(t["type"] == "function" for t in converted)
    assert {t["function"]["name"] for t in converted} == {s["name"] for s in TOOL_SCHEMAS}


# Loop behaviour -------------------------------------------------------------

def test_direct_reply_no_tool_calls(store: Store) -> None:
    llm = StubLLM([_assistant("I can help — what's your order number?")])
    run = run_agent(store, "my ticket is broken", CHANNEL_CHAT, llm=llm)
    assert run.reply.startswith("I can help")
    assert len(run.trace) == 1
    assert run.trace[0].kind == "final"


def test_loop_executes_tool_then_replies(store: Store) -> None:
    llm = StubLLM(
        [
            _assistant(
                tool_calls=[_tc("c1", "look_up_order", {"order_id": "TS-100001"})]
            ),
            _assistant("Found your order — resending your ticket now."),
        ]
    )
    run = run_agent(store, "my QR isn't scanning, TS-100001", CHANNEL_CHAT, llm=llm)
    kinds = [t.kind for t in run.trace]
    assert kinds == ["tool_call", "tool_result", "final"]
    assert run.trace[0].name == "look_up_order"
    assert run.trace[1].result["found"] is True


def test_loop_captures_escalation(store: Store) -> None:
    llm = StubLLM(
        [
            _assistant(
                tool_calls=[
                    _tc(
                        "e1",
                        "escalate_to_human",
                        {
                            "reason": "suspected_fraud",
                            "summary": "Seller account flagged, payment captured, ticket never delivered.",
                            "order_id": "TS-100007",
                        },
                    )
                ]
            ),
            _assistant("I've handed this to a teammate — they'll get back to you."),
        ]
    )
    run = run_agent(store, "i think i was scammed", CHANNEL_CHAT, llm=llm)
    assert run.escalation is not None
    assert run.escalation["reason_code"] == "suspected_fraud"


def test_loop_recovers_from_bad_tool_args(store: Store) -> None:
    llm = StubLLM(
        [
            _assistant(tool_calls=[_tc("c1", "look_up_order", {"unknown_field": "x"})]),
            _assistant("Could you share the order ID, please?"),
        ]
    )
    run = run_agent(store, "help", CHANNEL_CHAT, llm=llm)
    # Bad args manifests as the look_up_order branch falling through, not crashing
    result = run.trace[1].result
    assert "found" in result or "error" in result


def test_loop_max_steps_falls_back_to_escalation(store: Store) -> None:
    looping = [
        _assistant(tool_calls=[_tc(f"c{i}", "look_up_order", {"order_id": "TS-100001"})])
        for i in range(20)
    ]
    llm = StubLLM(looping)
    run = run_agent(store, "loop please", CHANNEL_CHAT, llm=llm, max_steps=3)
    assert run.escalation is not None
    assert run.escalation["reason_code"] == "policy_ambiguous"


def test_system_prompt_includes_current_time(store: Store) -> None:
    llm = StubLLM([_assistant("ok")])
    run_agent(store, "hi", CHANNEL_CHAT, llm=llm)
    sys_msg = llm.seen[0][0]
    assert sys_msg["role"] == "system"
    assert store.now.isoformat() in sys_msg["content"]


def test_history_is_threaded_into_messages(store: Store) -> None:
    llm = StubLLM([_assistant("got it")])
    history = [
        {"role": "user", "content": "i was here earlier"},
        {"role": "assistant", "content": "yep, what can i help with?"},
    ]
    run_agent(store, "the ticket bounced", CHANNEL_CHAT, history=history, llm=llm)
    seen = llm.seen[0]
    # system, user-history, assistant-history, current-user
    assert [m["role"] for m in seen] == ["system", "user", "assistant", "user"]
