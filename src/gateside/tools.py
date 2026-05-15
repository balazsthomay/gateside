"""Tools exposed to the agent.

Each tool is a plain function. Schemas are declared once below for the LLM, and
``TOOL_DISPATCH`` maps schema names to the implementing function. No registry
class, no decorator magic — the reviewer can read every tool in one pass.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

from gateside.backend import Store, event_phase


class EscalationReason(str, Enum):
    """The structured reason codes a human must see when the agent hands off.

    Adding a new code is a design decision — keep this list short and meaningful.
    """

    SELLER_UNREACHABLE_EVENT_IMMINENT = "seller_unreachable_event_imminent"
    SUSPECTED_FRAUD = "suspected_fraud"
    PAYMENT_DISPUTE_REQUIRED = "payment_dispute_required"
    POST_EVENT_REFUND_REVIEW = "post_event_refund_review"
    OUT_OF_SCOPE = "out_of_scope"
    POLICY_AMBIGUOUS = "policy_ambiguous"


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------

_PUBLIC_ORDER_KEYS = {
    "order_id",
    "buyer_email",
    "ticket",
    "event",
    "seller",
    "payment",
    "secureswap_protected",
    "notes",
}


def _public_view(order: dict[str, Any]) -> dict[str, Any]:
    """Strip internal-only keys (like ``scenario_hint``) before exposing an order to the LLM."""
    return {k: v for k, v in order.items() if k in _PUBLIC_ORDER_KEYS}


def look_up_order(
    store: Store,
    order_id: str | None = None,
    buyer_email: str | None = None,
) -> dict[str, Any]:
    """Find one order by ID or by buyer email.

    Returns ``{"found": True, "order": {...}}`` on a single match,
    ``{"found": False, "reason": "...", "candidates": [...]}`` when ambiguous
    or missing. The agent decides what to do next; this is the only read path.
    """
    if order_id:
        order = store.get_order(order_id)
        if order is None:
            return {"found": False, "reason": "not_found_by_order_id"}
        return {"found": True, "order": _public_view(order)}
    if buyer_email:
        matches = store.find_by_email(buyer_email)
        if not matches:
            return {"found": False, "reason": "not_found_by_email"}
        if len(matches) > 1:
            return {
                "found": False,
                "reason": "ambiguous_multiple_orders",
                "candidates": [
                    {
                        "order_id": m["order_id"],
                        "event_name": m["event"]["name"],
                        "starts_at": m["event"]["starts_at"],
                    }
                    for m in matches
                ],
            }
        return {"found": True, "order": _public_view(matches[0])}
    return {"found": False, "reason": "no_identifier_supplied"}


def attempt_resend_ticket(store: Store, order_id: str) -> dict[str, Any]:
    """Try to regenerate and resend the ticket QR.

    Refuses if the ticket is in any non-resendable state (used, cancelled,
    transferred, pending). Increments ``qr_resend_count`` on success.
    """
    order = store.get_order(order_id)
    if order is None:
        return {"resent": False, "reason": "order_not_found"}
    status = order["ticket"]["status"]
    if status != "valid":
        return {"resent": False, "reason": f"ticket_status_{status}"}
    order["ticket"]["qr_resend_count"] += 1
    if order["ticket"].get("delivery_status") in {"queued", "corrupt_pdf", "stuck"}:
        order["ticket"]["delivery_status"] = "delivered"
    store.log_side_effect("resend_ticket", order_id=order_id)
    return {
        "resent": True,
        "new_qr_resend_count": order["ticket"]["qr_resend_count"],
        "delivered_to": order["buyer_email"],
    }


def contact_seller_via_aftersales(store: Store, order_id: str) -> dict[str, Any]:
    """Open an Aftersales-mediated request to the seller. Mock latency outcome.

    Maps the seller's ``responsiveness`` to a deterministic response shape so
    eval scenarios behave the same way every run.
    """
    order = store.get_order(order_id)
    if order is None:
        return {"request_sent": False, "reason": "order_not_found"}
    responsiveness = order["seller"]["responsiveness"]
    response = {
        "responsive": {"status": "responded", "eta_minutes": 3, "message": "Acknowledged, checking."},
        "slow": {"status": "responded", "eta_minutes": 35, "message": "Will look as soon as I can."},
        "unreachable": {"status": "no_response", "eta_minutes": None},
    }[responsiveness]
    store.log_side_effect("contact_seller", order_id=order_id, outcome=response["status"])
    return {"request_sent": True, "seller_response": response}


def check_refund_eligibility(
    store: Store, order_id: str, buyer_claim: str
) -> dict[str, Any]:
    """Assess whether the case is eligible for human refund review.

    *This tool does not issue refunds.* It returns a structured assessment that
    a human reviewer (or the agent's escalation step) uses to decide. The agent
    must not promise refund amounts or timing to the buyer based on this output.
    """
    order = store.get_order(order_id)
    if order is None:
        return {"eligible_for_review": False, "reason": "order_not_found"}

    constraints_checked: list[str] = []
    notes: list[str] = []

    # 30-day post-event window
    event_start = datetime.fromisoformat(order["event"]["starts_at"].replace("Z", "+00:00"))
    if (store.now - event_start) > timedelta(days=30):
        return {
            "eligible_for_review": False,
            "reason": "outside_30_day_window",
            "constraints_checked": ["post_event_30d_window"],
        }
    constraints_checked.append("post_event_30d_window")

    status = order["ticket"]["status"]
    phase = event_phase(order["event"], store.now)

    # Cancelled tickets or stuck-delivery == automatic review candidate.
    if status == "cancelled" or order["ticket"].get("delivery_status") in {"queued", "stuck", "corrupt_pdf"}:
        notes.append("ticket delivery or status indicates platform-side failure")
        return {
            "eligible_for_review": True,
            "reasoning": "ticket never delivered or seller cancelled — strong refund signal",
            "constraints_checked": constraints_checked,
            "notes": notes,
        }

    # Used ticket post-event with low resend signal: likely buyer attended, low signal.
    if status == "used" and phase == "ended":
        # Distinguishing signal: did the buyer try to resend repeatedly?
        if order["ticket"].get("qr_resend_count", 0) >= 1:
            return {
                "eligible_for_review": True,
                "reasoning": "resend attempts in support log suggest buyer never made it in",
                "constraints_checked": constraints_checked,
                "notes": notes,
            }
        return {
            "eligible_for_review": False,
            "reason": "no_invalidity_signal",
            "constraints_checked": constraints_checked,
            "notes": ["used ticket with no resend attempts — buyer likely entered"],
        }

    # Pending payment / suspected fraud account = review path
    if order["seller"].get("account_flagged") or order["payment"]["status"] != "captured":
        return {
            "eligible_for_review": True,
            "reasoning": "seller account flagged or payment irregular — escalate for fraud review",
            "constraints_checked": constraints_checked,
            "notes": notes,
        }

    # Default: keep review path open, surface live phase to the reviewer.
    notes.append(f"event_phase={phase}; buyer_claim={buyer_claim!r}")
    return {
        "eligible_for_review": True,
        "reasoning": "no automatic disqualifier; human Aftersales must evaluate evidence",
        "constraints_checked": constraints_checked,
        "notes": notes,
    }


def escalate_to_human(
    store: Store,
    reason: EscalationReason,
    summary: str,
    order_id: str | None = None,
) -> dict[str, Any]:
    """Hand the case off to Aftersales with a structured reason code.

    The reason MUST be one of ``EscalationReason``. The summary is a one-line
    handoff note the human will read first.
    """
    if not isinstance(reason, EscalationReason):
        try:
            reason = EscalationReason(reason)
        except ValueError as exc:
            raise ValueError(f"unknown escalation reason: {reason!r}") from exc
    case_id = f"CASE-{secrets.token_hex(3).upper()}"
    store.log_side_effect(
        "escalate", case_id=case_id, reason=reason.value, order_id=order_id, summary=summary
    )
    return {
        "escalated": True,
        "case_id": case_id,
        "reason_code": reason.value,
        "handoff_summary": summary,
        "next_step": "An Aftersales teammate will pick this up.",
    }


# ---------------------------------------------------------------------------
# Schemas — Anthropic / OpenRouter tool-use shape
# ---------------------------------------------------------------------------

TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "name": "look_up_order",
        "description": (
            "Find a TicketSwap order by order_id or by buyer_email. Returns the order or "
            "an ambiguous-match list. Prefer order_id when known. Never invent order details "
            "— if the tool says not_found, the order does not exist."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "order_id": {"type": "string", "description": "Format: TS-NNNNNN"},
                "buyer_email": {"type": "string", "description": "Buyer's email address"},
            },
        },
    },
    {
        "name": "attempt_resend_ticket",
        "description": (
            "Regenerate and resend the QR ticket to the buyer's email. Only works on tickets "
            "in 'valid' status. Refuses on used, cancelled, transferred, or pending tickets."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"order_id": {"type": "string"}},
            "required": ["order_id"],
        },
    },
    {
        "name": "contact_seller_via_aftersales",
        "description": (
            "Open an Aftersales-mediated request to the seller. The seller may respond fast, slow, "
            "or not at all. Use this when the ticket itself looks correct but the buyer reports it "
            "doesn't work, or when seller action is needed."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"order_id": {"type": "string"}},
            "required": ["order_id"],
        },
    },
    {
        "name": "check_refund_eligibility",
        "description": (
            "Assess whether this case qualifies for human refund review. Returns a structured "
            "assessment — it does NOT issue a refund and does NOT commit to amounts. The agent "
            "must not promise refund timing or amounts based on this output. Respects the 30-day "
            "post-event window."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "order_id": {"type": "string"},
                "buyer_claim": {
                    "type": "string",
                    "description": "Plain-English summary of what the buyer says happened",
                },
            },
            "required": ["order_id", "buyer_claim"],
        },
    },
    {
        "name": "escalate_to_human",
        "description": (
            "Hand the case off to an Aftersales teammate with a structured reason code. Always "
            "include a one-line summary the human reads first. Reason codes: "
            "seller_unreachable_event_imminent, suspected_fraud, payment_dispute_required, "
            "post_event_refund_review, out_of_scope, policy_ambiguous."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "reason": {
                    "type": "string",
                    "enum": [r.value for r in EscalationReason],
                },
                "summary": {"type": "string"},
                "order_id": {"type": "string"},
            },
            "required": ["reason", "summary"],
        },
    },
]


TOOL_DISPATCH = {
    "look_up_order": look_up_order,
    "attempt_resend_ticket": attempt_resend_ticket,
    "contact_seller_via_aftersales": contact_seller_via_aftersales,
    "check_refund_eligibility": check_refund_eligibility,
    "escalate_to_human": escalate_to_human,
}


def run_tool(store: Store, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Dispatch a tool call from the LLM. Wraps errors into a stable result shape."""
    fn = TOOL_DISPATCH.get(name)
    if fn is None:
        return {"error": f"unknown_tool: {name}"}
    try:
        if name == "escalate_to_human" and "reason" in arguments:
            arguments = {**arguments, "reason": EscalationReason(arguments["reason"])}
        return fn(store, **arguments)
    except TypeError as exc:
        return {"error": "bad_arguments", "detail": str(exc)}
    except ValueError as exc:
        return {"error": "bad_value", "detail": str(exc)}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
