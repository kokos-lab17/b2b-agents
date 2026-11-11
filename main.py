"""Main FastAPI application for Autonomous Multi-Agent B2B Procurement."""
import asyncio
import logging
import re
import threading
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from backend.config import settings
from backend.memory import record_payment_captured
from backend.negotiation import NegotiationEngine
from backend.paypal import PayPalClient, PayPalError

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("main")

app = FastAPI(title="Autonomous Multi-Agent B2B Procurement")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.FRONTEND_ALLOWED_ORIGIN] if settings.FRONTEND_ALLOWED_ORIGIN != "*" else ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# In-memory shared state for active run
events: list[dict[str, Any]] = []
order_id: str | None = None
approve_url: str | None = None
status: str = "idle"
current_vendors: list[dict[str, Any]] = []
current_deal: dict[str, Any] | None = None
current_token_usage: dict[str, Any] | None = None

_lock = threading.Lock()
_generation = 0


class StartRequest(BaseModel):
    product: str = Field(..., min_length=1)
    qty: int = Field(..., gt=0)
    budget: float = Field(..., gt=0)
    demo_mode: bool = False


def _append_event(event: dict[str, Any], generation: int | None = None) -> None:
    with _lock:
        if generation is not None and generation != _generation:
            return
        if "step" not in event:
            event["step"] = len(events) + 1
        events.append(event)


def _run_procurement_worker(
    product: str, qty: int, budget: float, demo_mode: bool, generation: int
) -> None:
    global order_id, approve_url, status, current_vendors, current_deal, current_token_usage

    def on_event(evt: dict[str, Any]) -> None:
        _append_event(evt, generation)

    def on_vendors(vendors: list[dict[str, Any]]) -> None:
        global current_vendors
        with _lock:
            if generation != _generation:
                return
            current_vendors = vendors

    try:
        engine = NegotiationEngine(
            product=product,
            qty=qty,
            budget=budget,
            demo_mode=demo_mode,
            on_event=on_event,
            on_vendors=on_vendors,
        )
        result = engine.run()

        with _lock:
            if generation != _generation:
                return
            order_id = result.get("order_id")
            approve_url = result.get("approve_url")
            status = result.get("status", "completed")
            current_deal = result.get("deal")
            current_token_usage = result.get("token_usage")
            if result.get("vendors"):
                current_vendors = result["vendors"]

    except Exception as exc:
        logger.exception("Procurement execution failed")
        with _lock:
            if generation != _generation:
                return
            status = "failed"
        _append_event(
            {
                "agent": "System",
                "message": f"Procurement failed: {exc}",
                "offer": None,
                "status": "failed",
                "event_type": "failed",
            },
            generation,
        )


@app.post("/api/start")
async def start(body: StartRequest):
    """Initiates an autonomous procurement session."""
    global events, order_id, approve_url, status, current_vendors, current_deal, current_token_usage, _generation
    _generation += 1
    generation = _generation

    with _lock:
        events = []
        current_vendors = []
        current_deal = None
        current_token_usage = None
    order_id = None
    approve_url = None
    status = "negotiating"

    thread = threading.Thread(
        target=_run_procurement_worker,
        args=(body.product, body.qty, body.budget, body.demo_mode, generation),
        daemon=True,
    )
    thread.start()
    return {"status": "started"}


@app.get("/api/status")
async def get_status():
    """Returns current procurement events, vendors, deal details, and approval info."""
    with _lock:
        snapshot = list(events)
        vendors = list(current_vendors)
        deal_snap = dict(current_deal) if current_deal else None
        usage_snap = dict(current_token_usage) if current_token_usage else None

    return {
        "events": snapshot,
        "order_id": order_id,
        "approve_url": approve_url,
        "status": status,
        "vendors": vendors,
        "deal": deal_snap,
        "token_usage": usage_snap,
    }


@app.get("/api/confirm-payment")
async def confirm_payment(token: str):
    """Validates order token, captures approved PayPal Sandbox payment, and updates memory."""
    global status
    # Match valid PayPal order ID or DEMO ID
    if not (re.fullmatch(r"[A-Z0-9]{17,32}", token) or token.startswith("DEMO-")):
        raise HTTPException(status_code=400, detail="token must be a valid PayPal order id")

    with _lock:
        expected_order_id = order_id

    # Security check: Reject tokens unless they match the active pending order
    if not expected_order_id or token != expected_order_id:
        raise HTTPException(
            status_code=400,
            detail=f"Token mismatch: Provided token '{token}' does not match active pending order '{expected_order_id}'",
        )

    if token.startswith("DEMO-"):
        # Simulated demo capture: Only succeeds if active record exists and was updated
        captured = record_payment_captured(token)
        if not captured:
            raise HTTPException(
                status_code=400,
                detail=f"No pending transaction found matching demo token '{token}' or already captured.",
            )
        status = "captured"
        _append_event(
            {
                "agent": "System",
                "message": "[DEMO SIMULATION] Payment captured in demo mode. Transaction recorded in persistent memory.",
                "status": "captured",
                "event_type": "payment_captured",
            }
        )
        return {"status": "captured", "order_id": token, "mode": "demo"}

    # Live PayPal Capture
    try:
        client = PayPalClient()
        await asyncio.to_thread(client.capture_order, token)
        captured = record_payment_captured(token)
        if not captured:
            raise HTTPException(
                status_code=500,
                detail=f"Payment captured via PayPal Sandbox, but database record for order '{token}' failed to update.",
            )
    except PayPalError as exc:
        logger.error("PayPal capture error: %s", exc)
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Unexpected error capturing payment: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    status = "captured"
    _append_event(
        {
            "agent": "System",
            "message": "Payment captured. Transaction complete and settled via PayPal Sandbox.",
            "status": "captured",
            "event_type": "payment_captured",
        }
    )
    return {"status": "captured", "order_id": token}


@app.get("/api/health")
async def health():
    """Health check endpoint for deployment monitoring."""
    return {"status": "healthy", "service": "b2b-agents-backend"}
