"""Mock TicketSwap backend.

Loads fixed JSON state from ``data/orders.json`` and exposes a tiny in-memory store
that tools mutate at runtime. The store is process-local — each fresh API process
starts from the same seed, which keeps demo runs deterministic and reviewer-friendly.
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DATA_PATH = Path(__file__).resolve().parents[2] / "data" / "orders.json"


@dataclass
class Store:
    now: datetime
    orders: dict[str, dict[str, Any]]
    # side-effect log surfaced to the UI/trace
    side_effects: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def from_file(cls, path: Path = DATA_PATH) -> "Store":
        raw = json.loads(path.read_text())
        orders = {o["order_id"]: copy.deepcopy(o) for o in raw["orders"]}
        now = datetime.fromisoformat(raw["now"].replace("Z", "+00:00"))
        return cls(now=now, orders=orders)

    def reset(self) -> None:
        fresh = Store.from_file()
        self.orders = fresh.orders
        self.side_effects = []

    def get_order(self, order_id: str) -> dict[str, Any] | None:
        return self.orders.get(order_id)

    def find_by_email(self, email: str) -> list[dict[str, Any]]:
        return [o for o in self.orders.values() if o["buyer_email"].lower() == email.lower()]

    def log_side_effect(self, kind: str, **payload: Any) -> None:
        self.side_effects.append({"kind": kind, "at": self.now.isoformat(), **payload})


def event_phase(event: dict[str, Any], now: datetime) -> str:
    """Compute live phase for an event based on ``now``."""
    starts_at = datetime.fromisoformat(event["starts_at"].replace("Z", "+00:00"))
    delta_min = (starts_at - now).total_seconds() / 60
    if event.get("status") == "ended":
        return "ended"
    if delta_min < -240:
        return "ended"
    if delta_min < 0:
        return "in_progress"
    if delta_min < 90:
        return "imminent"
    if delta_min < 60 * 24:
        return "today"
    return "upcoming"


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
