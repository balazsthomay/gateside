"""Cover the dispatch error paths and remaining schema-coercion edges."""

from __future__ import annotations

import pytest

from gateside.backend import Store
from gateside.tools import EscalationReason, escalate_to_human, run_tool


@pytest.fixture
def store() -> Store:
    return Store.from_file()


def test_run_tool_unknown_name(store: Store) -> None:
    out = run_tool(store, "no_such_tool", {})
    assert out == {"error": "unknown_tool: no_such_tool"}


def test_run_tool_bad_arguments_returns_error_shape(store: Store) -> None:
    # look_up_order accepts only order_id / buyer_email — pass a positional impossibility
    out = run_tool(store, "look_up_order", {"unknown_field": "x"})
    assert "error" in out


def test_run_tool_bad_value_returns_error_shape(store: Store) -> None:
    out = run_tool(
        store,
        "escalate_to_human",
        {"reason": "not_a_real_code", "summary": "x"},
    )
    assert "error" in out


def test_escalate_accepts_string_reason_via_dispatch(store: Store) -> None:
    out = run_tool(
        store,
        "escalate_to_human",
        {"reason": "suspected_fraud", "summary": "x", "order_id": "TS-100007"},
    )
    assert out["escalated"] is True
    assert out["reason_code"] == "suspected_fraud"


def test_escalate_direct_call_with_enum(store: Store) -> None:
    out = escalate_to_human(
        store, reason=EscalationReason.OUT_OF_SCOPE, summary="hi"
    )
    assert out["reason_code"] == "out_of_scope"


def test_resend_handles_pending_status(store: Store) -> None:
    # TS-100019 has payment pending and ticket pending
    from gateside.tools import attempt_resend_ticket

    out = attempt_resend_ticket(store, order_id="TS-100019")
    assert out["resent"] is False
    assert out["reason"] == "ticket_status_pending"


def test_contact_seller_slow_response(store: Store) -> None:
    from gateside.tools import contact_seller_via_aftersales

    # TS-100002 has seller responsiveness=slow
    out = contact_seller_via_aftersales(store, order_id="TS-100002")
    assert out["seller_response"]["status"] == "responded"
    assert out["seller_response"]["eta_minutes"] == 35
