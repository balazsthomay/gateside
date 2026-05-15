from datetime import datetime, timezone

import pytest

from gateside.backend import Store, event_phase


@pytest.fixture
def store() -> Store:
    return Store.from_file()


def test_store_loads_twenty_orders(store: Store) -> None:
    assert len(store.orders) == 20


def test_get_order_returns_none_for_unknown(store: Store) -> None:
    assert store.get_order("TS-DOES-NOT-EXIST") is None


def test_get_order_returns_dict(store: Store) -> None:
    order = store.get_order("TS-100001")
    assert order is not None
    assert order["buyer_email"] == "sam.k@example.com"


def test_find_by_email_is_case_insensitive(store: Store) -> None:
    matches = store.find_by_email("MAUD.R@example.com")
    assert {o["order_id"] for o in matches} == {"TS-100014", "TS-100015"}


def test_find_by_email_unknown_returns_empty(store: Store) -> None:
    assert store.find_by_email("nobody@example.com") == []


def test_reset_restores_state(store: Store) -> None:
    store.orders["TS-100001"]["ticket"]["status"] = "used"
    store.reset()
    assert store.get_order("TS-100001")["ticket"]["status"] == "valid"
    assert store.side_effects == []


def test_log_side_effect_records_kind_and_time(store: Store) -> None:
    store.log_side_effect("resend", order_id="TS-100001")
    assert store.side_effects[0]["kind"] == "resend"
    assert store.side_effects[0]["order_id"] == "TS-100001"
    assert "at" in store.side_effects[0]


def test_event_phase_ended() -> None:
    now = datetime(2026, 5, 15, 20, 0, tzinfo=timezone.utc)
    event = {"starts_at": "2026-05-13T20:00:00Z", "status": "ended"}
    assert event_phase(event, now) == "ended"


def test_event_phase_in_progress() -> None:
    now = datetime(2026, 5, 15, 20, 0, tzinfo=timezone.utc)
    event = {"starts_at": "2026-05-15T19:30:00Z", "status": "in_progress"}
    assert event_phase(event, now) == "in_progress"


def test_event_phase_imminent_within_90_min() -> None:
    now = datetime(2026, 5, 15, 20, 0, tzinfo=timezone.utc)
    event = {"starts_at": "2026-05-15T20:30:00Z", "status": "today"}
    assert event_phase(event, now) == "imminent"


def test_event_phase_today() -> None:
    now = datetime(2026, 5, 15, 20, 0, tzinfo=timezone.utc)
    event = {"starts_at": "2026-05-15T23:00:00Z", "status": "today"}
    assert event_phase(event, now) == "today"


def test_event_phase_upcoming() -> None:
    now = datetime(2026, 5, 15, 20, 0, tzinfo=timezone.utc)
    event = {"starts_at": "2026-06-15T20:00:00Z", "status": "upcoming"}
    assert event_phase(event, now) == "upcoming"
