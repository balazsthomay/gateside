"""FastAPI surface for the demo UI.

One endpoint runs the agent and returns the reply + a serialized trace, so the
frontend can show what tools were called and which (if any) escalation reason
the agent picked. Each request gets a fresh ``Store`` so the demo is
deterministic for the next reviewer.
"""

from __future__ import annotations

import os
from dataclasses import asdict
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")

from gateside.agent import run_agent
from gateside.backend import Store
from gateside.eval import EVAL_SCENARIOS


class TurnRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    channel: str = Field(pattern="^(chat|email)$")
    history: list[dict[str, Any]] = Field(default_factory=list)


class TurnResponse(BaseModel):
    reply: str
    trace: list[dict[str, Any]]
    escalation: dict[str, Any] | None
    messages: list[dict[str, Any]]


def create_app(store_factory=Store.from_file) -> FastAPI:
    app = FastAPI(title="Gateside")

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/scenarios")
    def scenarios() -> list[dict[str, Any]]:
        return [
            {
                "id": s.id,
                "title": s.title,
                "channel": s.channel,
                "user_message": s.user_message,
                "notes": s.notes,
            }
            for s in EVAL_SCENARIOS
        ]

    @app.post("/api/turn", response_model=TurnResponse)
    def turn(req: TurnRequest) -> TurnResponse:
        if not os.environ.get("OPENROUTER_API_KEY"):
            raise HTTPException(status_code=500, detail="OPENROUTER_API_KEY not configured")
        store = store_factory()
        run = run_agent(store, req.message, req.channel, history=req.history)
        return TurnResponse(
            reply=run.reply,
            trace=[asdict(s) for s in run.trace],
            escalation=run.escalation,
            messages=run.messages,
        )

    web_dir = ROOT / "web"
    if web_dir.exists():
        app.mount("/static", StaticFiles(directory=str(web_dir)), name="static")

        @app.get("/")
        def index() -> FileResponse:
            return FileResponse(str(web_dir / "index.html"))

    return app


app = create_app()
