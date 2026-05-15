import pytest

from gateside.backend import Store
from gateside.tools import (
    TOOL_SCHEMAS,
    attempt_resend_ticket,
    check_refund_eligibility,
    contact_seller_via_aftersales,
    escalate_to_human,
    look_up_order,
    EscalationReason,
)


@pytest.fixture
def store() -> Store:
    return Store.from_file()


# look_up_order ---------------------------------------------------------------

def test_look_up_order_by_order_id_returns_summary(store: Store) -> None:
    result = look_up_order(store, order_id="TS-100001")
    assert result["found"] is True
    assert result["order"]["order_id"] == "TS-100001"
    assert "ticket" in result["order"]
    assert "scenario_hint" not in result["order"], "internal hint must not leak"


def test_look_up_order_unknown_id_returns_not_found(store: Store) -> None:
    result = look_up_order(store, order_id="TS-DOES-NOT-EXIST")
    assert result["found"] is False
    assert "not_found" in result["reason"]


def test_look_up_order_by_email_single_match(store: Store) -> None:
    result = look_up_order(store, buyer_email="sam.k@example.com")
    assert result["found"] is True
    assert result["order"]["order_id"] == "TS-100001"


def test_look_up_order_by_email_multiple_matches_returns_ambiguous(store: Store) -> None:
    result = look_up_order(store, buyer_email="maud.r@example.com")
    assert result["found"] is False
    assert result["reason"] == "ambiguous_multiple_orders"
    assert len(result["candidates"]) == 2


def test_look_up_order_requires_one_field(store: Store) -> None:
    result = look_up_order(store)
    assert result["found"] is False
    assert result["reason"] == "no_identifier_supplied"


# attempt_resend_ticket -------------------------------------------------------

def test_resend_succeeds_for_valid_ticket(store: Store) -> None:
    result = attempt_resend_ticket(store, order_id="TS-100001")
    assert result["resent"] is True
    assert result["new_qr_resend_count"] == 1
    assert store.get_order("TS-100001")["ticket"]["qr_resend_count"] == 1


def test_resend_refuses_used_ticket(store: Store) -> None:
    result = attempt_resend_ticket(store, order_id="TS-100002")
    assert result["resent"] is False
    assert result["reason"] == "ticket_status_used"


def test_resend_refuses_cancelled_ticket(store: Store) -> None:
    result = attempt_resend_ticket(store, order_id="TS-100006")
    assert result["resent"] is False
    assert result["reason"] == "ticket_status_cancelled"


def test_resend_refuses_unknown_order(store: Store) -> None:
    result = attempt_resend_ticket(store, order_id="TS-NOPE")
    assert result["resent"] is False
    assert result["reason"] == "order_not_found"


# contact_seller_via_aftersales ----------------------------------------------

def test_contact_seller_responsive_seller(store: Store) -> None:
    result = contact_seller_via_aftersales(store, order_id="TS-100001")
    assert result["request_sent"] is True
    assert result["seller_response"]["status"] == "responded"


def test_contact_seller_unreachable_seller(store: Store) -> None:
    result = contact_seller_via_aftersales(store, order_id="TS-100006")
    assert result["request_sent"] is True
    assert result["seller_response"]["status"] == "no_response"


def test_contact_seller_unknown_order(store: Store) -> None:
    result = contact_seller_via_aftersales(store, order_id="TS-NOPE")
    assert result["request_sent"] is False
    assert result["reason"] == "order_not_found"


# check_refund_eligibility ---------------------------------------------------

def test_refund_eligibility_flags_for_review_not_decides(store: Store) -> None:
    result = check_refund_eligibility(store, order_id="TS-100006", buyer_claim="seller cancelled")
    # Tool must NEVER commit a refund — only assess + flag.
    assert "refund_issued" not in result
    assert result["eligible_for_review"] is True
    assert "reasoning" in result


def test_refund_eligibility_blocks_entered_venue(store: Store) -> None:
    # If the buyer's claim implies they entered the venue, the tool must surface the waiver.
    result = check_refund_eligibility(
        store, order_id="TS-100008", buyer_claim="couldn't get in two nights ago, want refund"
    )
    assert result["eligible_for_review"] is True
    # Buyer never got in (status used but resend log shows attempts) — case open
    assert "post_event_30d_window" in result["constraints_checked"]


def test_refund_eligibility_event_ended_no_show_low_signal(store: Store) -> None:
    # No-show on an ended event the buyer attended → not eligible.
    result = check_refund_eligibility(
        store, order_id="TS-100018", buyer_claim="want refund, didn't enjoy it"
    )
    assert result["eligible_for_review"] is False
    assert result["reason"] == "no_invalidity_signal"


def test_refund_eligibility_past_30_day_window(store: Store) -> None:
    # Force a now far in the future.
    from datetime import datetime, timezone
    store.now = datetime(2026, 7, 1, tzinfo=timezone.utc)
    result = check_refund_eligibility(
        store, order_id="TS-100018", buyer_claim="ticket didn't work"
    )
    assert result["eligible_for_review"] is False
    assert result["reason"] == "outside_30_day_window"


# escalate_to_human -----------------------------------------------------------

def test_escalate_records_reason_code(store: Store) -> None:
    result = escalate_to_human(
        store,
        reason=EscalationReason.SELLER_UNREACHABLE_EVENT_IMMINENT,
        summary="Seller cancelled 2h before kickoff. Payment captured.",
        order_id="TS-100006",
    )
    assert result["escalated"] is True
    assert result["reason_code"] == "seller_unreachable_event_imminent"
    assert result["case_id"].startswith("CASE-")


def test_escalate_rejects_freeform_reason(store: Store) -> None:
    with pytest.raises((ValueError, TypeError)):
        escalate_to_human(
            store, reason="just because", summary="x", order_id="TS-100001"  # type: ignore[arg-type]
        )


# Schema export ---------------------------------------------------------------

def test_tool_schemas_are_anthropic_shape() -> None:
    assert isinstance(TOOL_SCHEMAS, list)
    assert len(TOOL_SCHEMAS) >= 5
    for schema in TOOL_SCHEMAS:
        assert "name" in schema
        assert "description" in schema
        assert "input_schema" in schema
        assert schema["input_schema"]["type"] == "object"


def test_tool_schema_names_match_dispatch_table() -> None:
    from gateside.tools import TOOL_DISPATCH

    schema_names = {s["name"] for s in TOOL_SCHEMAS}
    assert schema_names == set(TOOL_DISPATCH.keys())
