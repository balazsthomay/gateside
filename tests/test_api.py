"""API tests using a stubbed agent caller — no real LLM, no network."""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

from gateside.api import create_app
from gateside.backend import Store


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("OPENROUTER_API_KEY", "test")

    def fake_llm(messages: list[dict[str, Any]], tools: list[dict[str, Any]], model: str) -> dict[str, Any]:
        return {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "Hi — what's your order number?",
                    }
                }
            ]
        }

    # Patch the default llm caller used in the agent module
    from gateside import agent as agent_mod

    monkeypatch.setattr(agent_mod, "call_openrouter", fake_llm)
    return TestClient(create_app(store_factory=Store.from_file))


def test_health(client: TestClient) -> None:
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_scenarios_endpoint_lists_all(client: TestClient) -> None:
    r = client.get("/api/scenarios")
    assert r.status_code == 200
    body = r.json()
    assert len(body) >= 14
    assert all({"id", "title", "channel", "user_message"} <= set(s) for s in body)


def test_turn_chat(client: TestClient) -> None:
    r = client.post(
        "/api/turn",
        json={"message": "ticket broken", "channel": "chat", "history": []},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["reply"].startswith("Hi")
    assert isinstance(body["trace"], list)


def test_turn_rejects_unknown_channel(client: TestClient) -> None:
    r = client.post(
        "/api/turn",
        json={"message": "x", "channel": "voice", "history": []},
    )
    assert r.status_code == 422


def test_turn_requires_message(client: TestClient) -> None:
    r = client.post(
        "/api/turn",
        json={"message": "", "channel": "chat", "history": []},
    )
    assert r.status_code == 422


def test_turn_without_api_key_returns_500(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    client = TestClient(create_app(store_factory=Store.from_file))
    r = client.post(
        "/api/turn",
        json={"message": "hi", "channel": "chat", "history": []},
    )
    assert r.status_code == 500
