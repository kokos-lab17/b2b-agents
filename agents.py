"""Backward-compatible facade exposing run_procurement and capture_payment."""
from typing import Any, Callable

from backend.memory import record_payment_captured
from backend.negotiation import NegotiationEngine
from backend.paypal import PayPalClient


def capture_payment(order_id: str) -> dict[str, Any]:
    """Capture an approved PayPal order and record in persistent memory."""
    if order_id.startswith("DEMO-"):
        success = record_payment_captured(order_id)
        if not success:
            raise ValueError(f"No active demo order matching '{order_id}' found to capture.")
        return {"status": "COMPLETED", "order_id": order_id, "mode": "demo"}

    client = PayPalClient()
    result = client.capture_order(order_id)
    record_payment_captured(order_id)
    return result


def run_procurement(
    product: str,
    qty: int,
    budget: float,
    step_callback: Callable[[str, Any], None] | None = None,
    on_vendors: Callable[[list[dict[str, Any]]], None] | None = None,
    demo_mode: bool = False,
) -> dict[str, Any]:
    """Run procurement through the autonomous multi-agent negotiation engine."""
    def event_adapter(evt: dict[str, Any]):
        if step_callback:
            step_callback(evt["agent"], evt)

    engine = NegotiationEngine(
        product=product,
        qty=qty,
        budget=budget,
        demo_mode=demo_mode,
        on_event=event_adapter,
        on_vendors=on_vendors,
    )
    return engine.run()
