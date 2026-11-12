"""Main FastAPI application for Autonomous Multi-Agent B2B Procurement."""
import asyncio
import concurrent.futures
import json
import logging
import re
import threading
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from backend.buyer_preferences import (
    accept_recommendation,
    get_buyer_profile,
    get_pending_recommendations,
    get_saved_buyer_preferences,
    ignore_recommendation,
    resolve_effective_preferences,
    save_buyer_preferences,
)
from backend.config import settings
from backend.memory import (
    get_latest_session,
    get_overall_procurement_analytics,
    get_session,
    get_session_audit_trail,
    get_session_events,
    get_strategy_metrics,
    get_supplier_metrics,
    get_transaction,
    record_payment_captured,
    record_session,
    record_session_event,
    record_session_result,
)
from backend.models import (
    BuyerPreferencePriority,
    BuyerPreferences,
    NegotiationMode,
    PaymentMode,
    QuoteComparisonRequest,
    StartRequest,
    StatusResponse,
    WhatIfSimulationRequest,
)
from backend.negotiation import NegotiationEngine
from backend.paypal import PayPalClient, PayPalError
from backend.personas import SELLER_PERSONAS
from backend.quotes import compare_quotes
from backend.simulator import run_what_if_simulation

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("main")

app = FastAPI(
    title="Autonomous Multi-Agent B2B Procurement",
    description="Backend API powering token-efficient autonomous multi-agent procurement.",
    version="2.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.FRONTEND_ALLOWED_ORIGIN] if settings.FRONTEND_ALLOWED_ORIGIN != "*" else ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# In-Memory Session Registry with Thread Safety & Session Isolation
# ---------------------------------------------------------------------------
_lock = threading.Lock()
_active_sessions: dict[str, dict[str, Any]] = {}
_latest_session_id: str | None = None


def _get_session_state(session_id: str | None = None) -> dict[str, Any]:
    """Retrieve session state by ID or return latest session for backward compatibility."""
    with _lock:
        target_id = session_id or _latest_session_id
        if target_id and target_id in _active_sessions:
            s = _active_sessions[target_id]
            return {
                "session_id": target_id,
                "events": list(s.get("events", [])),
                "order_id": s.get("order_id"),
                "approve_url": s.get("approve_url"),
                "status": s.get("status", "idle"),
                "vendors": list(s.get("vendors", [])),
                "deal": dict(s["deal"]) if s.get("deal") else None,
                "final_offers": list(s.get("final_offers", [])),
                "winning_vendor": s.get("winning_vendor"),
                "winning_offer": dict(s["winning_offer"]) if s.get("winning_offer") else None,
                "buyer_intelligence": dict(s["buyer_intelligence"]) if s.get("buyer_intelligence") else None,
                "last_heartbeat": s.get("last_heartbeat"),
                "updated_at": s.get("updated_at"),
                "token_usage": dict(s["token_usage"]) if s.get("token_usage") else None,
                "negotiation_mode": s.get("negotiation_mode", "DEMO"),
                "payment_mode": s.get("payment_mode", "SIMULATED"),
            }

    # If not active in memory, attempt database fallback (target_id or latest session from DB)
    db_s = get_session(target_id) if target_id else get_latest_session()
    if db_s:
        sid = db_s["id"]
        db_events = get_session_events(sid)
        deal_data = None
        if db_s.get("deal_json"):
            try:
                deal_data = json.loads(db_s["deal_json"])
            except Exception:
                pass
        final_offers_data = []
        if db_s.get("final_offers_json"):
            try:
                final_offers_data = json.loads(db_s["final_offers_json"])
            except Exception:
                pass
        intel_data = None
        if db_s.get("buyer_intelligence_json"):
            try:
                intel_data = json.loads(db_s["buyer_intelligence_json"])
            except Exception:
                pass

        return {
            "session_id": sid,
            "events": db_events,
            "order_id": db_s.get("order_id"),
            "approve_url": db_s.get("approve_url"),
            "status": db_s.get("status", "completed"),
            "vendors": [],
            "deal": deal_data,
            "final_offers": final_offers_data,
            "winning_vendor": db_s.get("selected_supplier"),
            "winning_offer": deal_data,
            "buyer_intelligence": intel_data,
            "last_heartbeat": None,
            "updated_at": db_s.get("updated_at"),
            "token_usage": None,
            "negotiation_mode": db_s.get("negotiation_mode", "DEMO"),
            "payment_mode": db_s.get("payment_mode", "SIMULATED"),
        }

    # Default idle state
    return {
        "session_id": None,
        "events": [],
        "order_id": None,
        "approve_url": None,
        "status": "idle",
        "vendors": [],
        "deal": None,
        "final_offers": [],
        "winning_vendor": None,
        "winning_offer": None,
        "buyer_intelligence": None,
        "last_heartbeat": None,
        "updated_at": None,
        "token_usage": None,
        "negotiation_mode": "DEMO",
        "payment_mode": "SIMULATED",
    }


def _run_procurement_worker(
    session_id: str,
    product: str,
    qty: int,
    budget: float,
    demo_mode: bool,
    negotiation_mode: NegotiationMode | None,
    payment_mode: PaymentMode | None,
    buyer_id: str,
    preferences: BuyerPreferences | None,
    specifications: str = "",
) -> None:
    """Background worker executing the negotiation engine for a specific session."""
    logger.info("[%s] Stage 2: Worker started.", session_id)

    def on_event(evt: dict[str, Any]) -> None:
        now_iso = datetime.now(timezone.utc).isoformat()
        with _lock:
            s = _active_sessions.get(session_id)
            if s:
                s["events"].append(evt)
                s["last_heartbeat"] = now_iso
                s["updated_at"] = now_iso

    def on_vendors(vendors: list[dict[str, Any]]) -> None:
        now_iso = datetime.now(timezone.utc).isoformat()
        with _lock:
            s = _active_sessions.get(session_id)
            if s:
                s["vendors"] = vendors
                s["last_heartbeat"] = now_iso
                s["updated_at"] = now_iso

    try:
        engine = NegotiationEngine(
            product=product,
            qty=qty,
            budget=budget,
            demo_mode=demo_mode,
            negotiation_mode=negotiation_mode,
            payment_mode=payment_mode,
            buyer_id=buyer_id,
            preferences=preferences,
            specifications=specifications,
            session_id=session_id,
            on_event=on_event,
            on_vendors=on_vendors,
        )

        executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        try:
            future = executor.submit(engine.run)
            result = future.result(timeout=25.0)
        except concurrent.futures.TimeoutError:
            executor.shutdown(wait=False, cancel_futures=True)
            raise TimeoutError("Negotiation worker timed out after 25 seconds.")
        else:
            executor.shutdown(wait=False)

        now_iso = datetime.now(timezone.utc).isoformat()
        with _lock:
            s = _active_sessions.get(session_id)
            if s:
                s["order_id"] = result.get("order_id")
                s["approve_url"] = result.get("approve_url")
                s["status"] = result.get("status", "awaiting_approval")
                s["deal"] = result.get("deal")
                s["final_offers"] = result.get("final_offers", [])
                s["winning_vendor"] = result.get("winning_vendor")
                s["winning_offer"] = result.get("winning_offer")
                s["buyer_intelligence"] = result.get("buyer_intelligence")
                s["token_usage"] = result.get("token_usage")
                s["last_heartbeat"] = now_iso
                s["updated_at"] = now_iso
                if result.get("vendors"):
                    s["vendors"] = result["vendors"]

        # Persist complete outcome to SQLite for persistent recovery across reloads
        winning_deal = result.get("deal")
        record_session_result(
            session_id=session_id,
            status=result.get("status", "awaiting_approval"),
            deal=winning_deal,
            final_offers=result.get("final_offers", []),
            buyer_intelligence=result.get("buyer_intelligence"),
            order_id=result.get("order_id"),
            approve_url=result.get("approve_url"),
            selected_supplier=result.get("winning_vendor"),
            total_cost=winning_deal.get("total_cost") if winning_deal else None,
        )

        logger.info(
            "[%s] Stage 11: Worker completed successfully. Status: %s",
            session_id,
            s.get("status") if s else "unknown",
        )

    except Exception as exc:
        logger.error("[%s] Stage 11: Worker failed: %s", session_id, exc)
        now_iso = datetime.now(timezone.utc).isoformat()
        with _lock:
            s = _active_sessions.get(session_id)
            if s:
                s["status"] = "failed"
                s["last_heartbeat"] = now_iso
                s["updated_at"] = now_iso
                err_evt = {
                    "step": len(s["events"]) + 1,
                    "agent": "System",
                    "message": f"Procurement failed: {exc}",
                    "offer": None,
                    "status": "failed",
                    "event_type": "failed",
                }
                s["events"].append(err_evt)
        record_session(
            session_id=session_id,
            product=product,
            qty=qty,
            budget=budget,
            status="failed",
            is_demo=demo_mode,
            negotiation_mode=negotiation_mode.value if negotiation_mode else "DEMO",
            payment_mode=payment_mode.value if payment_mode else "SIMULATED",
            buyer_id=buyer_id,
        )
        record_session_event(
            session_id=session_id,
            agent="System",
            event_type="failed",
            message=f"Procurement failed: {exc}",
            status="failed",
        )


# ---------------------------------------------------------------------------
# Core Procurement Endpoints
# ---------------------------------------------------------------------------
@app.post("/api/start")
async def start(body: StartRequest):
    """Initiates an isolated autonomous procurement session."""
    global _latest_session_id
    session_id = f"proc-{uuid.uuid4().hex[:12]}"

    # Resolve buyer preferences using precedence hierarchy
    effective_preferences = resolve_effective_preferences(
        session_preferences=body.preferences,
        buyer_id=body.buyer_id,
    )

    # Determine mode defaults
    neg_mode = body.negotiation_mode
    if neg_mode is None:
        neg_mode = NegotiationMode.DEMO if body.demo_mode else (
            NegotiationMode.LIVE_AI if settings.GROQ_API_KEY else NegotiationMode.DEMO
        )

    pay_mode = body.payment_mode
    if pay_mode is None:
        pay_mode = PaymentMode.SIMULATED if body.demo_mode else PaymentMode.PAYPAL_SANDBOX

    # Enforce PayPal Hackathon requirement: Live AI mode requires genuine PayPal Sandbox settlement
    if neg_mode == NegotiationMode.LIVE_AI and pay_mode == PaymentMode.SIMULATED:
        raise HTTPException(
            status_code=400,
            detail="Live AI negotiation requires genuine PayPal Sandbox authorization. Simulated settlement is not permitted in Live AI mode.",
        )

    now_iso = datetime.now(timezone.utc).isoformat()
    logger.info(
        "[%s] Stage 1: Procurement request accepted. Product='%s', Qty=%d, Budget=$%.2f. Mode: %s/%s",
        session_id,
        body.product,
        body.qty,
        body.budget,
        neg_mode.value,
        pay_mode.value,
    )

    with _lock:
        _active_sessions[session_id] = {
            "session_id": session_id,
            "events": [],
            "order_id": None,
            "approve_url": None,
            "status": "negotiating",
            "vendors": [],
            "deal": None,
            "final_offers": [],
            "winning_vendor": None,
            "winning_offer": None,
            "buyer_intelligence": None,
            "token_usage": None,
            "negotiation_mode": neg_mode.value,
            "payment_mode": pay_mode.value,
            "buyer_id": body.buyer_id,
            "last_heartbeat": now_iso,
            "updated_at": now_iso,
        }
        _latest_session_id = session_id

    thread = threading.Thread(
        target=_run_procurement_worker,
        args=(
            session_id,
            body.product,
            body.qty,
            body.budget,
            body.demo_mode,
            neg_mode,
            pay_mode,
            body.buyer_id,
            effective_preferences,
            body.specifications,
        ),
        daemon=True,
    )
    thread.start()

    return {"status": "started", "session_id": session_id}



@app.get("/api/status")
async def get_status(session_id: str | None = None):
    """Returns procurement events, vendors, deal details, and approval info for a session."""
    return _get_session_state(session_id)


@app.get("/api/confirm-payment")
async def confirm_payment(
    token: str = Query(..., description="PayPal or Simulated order token"),
    session_id: str | None = Query(None, description="Optional session id"),
):
    """Validates order token, captures approved payment (Simulated or PayPal Sandbox), and updates memory."""
    # Match valid PayPal order ID or SIM/DEMO ID
    if not (re.fullmatch(r"[A-Z0-9]{17,32}", token) or token.startswith("DEMO-") or token.startswith("SIM-")):
        raise HTTPException(status_code=400, detail="token must be a valid PayPal order id or simulated token")

    target_session_id = session_id or _latest_session_id
    with _lock:
        s = _active_sessions.get(target_session_id) if target_session_id else None
        expected_order_id = s.get("order_id") if s else None

    tx = get_transaction(token)

    # Security check: Reject tokens unless they match an active pending order or exist in DB
    if not (expected_order_id and token == expected_order_id) and not tx:
        raise HTTPException(
            status_code=400,
            detail=f"Token mismatch: Provided token '{token}' does not match active pending order '{expected_order_id}'",
        )

    # Idempotent capture: if already captured, return success immediately
    if tx and tx.get("status") == "captured":
        if s:
            s["status"] = "captured"
        return {"status": "captured", "order_id": token, "already_captured": True}

    # Simulated Deal Settlement (Feature 3 & 11)
    if token.startswith("DEMO-") or token.startswith("SIM-"):
        captured = record_payment_captured(token)
        if not captured:
            raise HTTPException(
                status_code=400,
                detail=f"No pending transaction found matching simulated token '{token}'.",
            )
        with _lock:
            if s:
                s["status"] = "captured"
                s["events"].append({
                    "step": len(s["events"]) + 1,
                    "agent": "System",
                    "message": f"Simulated settlement complete for order {token}. Transaction recorded in persistent memory.",
                    "status": "captured",
                    "event_type": "payment_captured",
                })
        if target_session_id:
            record_session_event(
                session_id=target_session_id,
                agent="System",
                event_type="payment_captured",
                message=f"Simulated settlement complete for order {token}.",
                status="captured",
            )
        return {"status": "captured", "order_id": token, "mode": "simulated"}

    # Live PayPal Sandbox Capture Flow (Requirement 11)
    try:
        client = PayPalClient()

        # Step 1: Pre-capture validation: Verify PayPal order details, status, amount, and currency
        order_details = await asyncio.to_thread(client.get_order_details, token)
        order_status = order_details.get("status")

        # Validate currency and amount against the expected commercial deal BEFORE capture/completion
        if tx and tx.get("amount") is not None:
            expected_amount = float(tx["amount"])
            purchase_units = order_details.get("purchase_units", [])
            if not purchase_units:
                raise HTTPException(
                    status_code=400,
                    detail=f"Order '{token}' missing purchase units.",
                )
            unit_amount_info = purchase_units[0].get("amount", {})
            currency_code = unit_amount_info.get("currency_code", "USD")
            order_value = float(unit_amount_info.get("value", 0.0))
            if currency_code != "USD":
                raise HTTPException(
                    status_code=400,
                    detail=f"Currency mismatch for order '{token}': Expected 'USD', found '{currency_code}'.",
                )
            if abs(order_value - expected_amount) > 0.01:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"Amount mismatch for order '{token}': Expected ${expected_amount:.2f}, "
                        f"PayPal order is ${order_value:.2f}."
                    ),
                )

        # If already captured on PayPal, record and return idempotent success
        if order_status == "COMPLETED":
            record_payment_captured(token)
            if s:
                s["status"] = "captured"
            return {"status": "captured", "order_id": token, "already_captured": True}

        # Creating an order must NOT capture money. Buyer must first approve on PayPal.
        if order_status != "APPROVED":
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Cannot capture PayPal order '{token}': Order status is '{order_status}', "
                    "expected 'APPROVED'. Buyer has not approved this order on PayPal yet."
                ),
            )

        # Step 2: Capture verified order only after buyer approval and terms validation
        capture_result = await asyncio.to_thread(client.capture_order, token)
        if capture_result.get("status") != "COMPLETED":
            raise HTTPException(
                status_code=502,
                detail=f"PayPal capture failed: Final status is '{capture_result.get('status')}', expected 'COMPLETED'.",
            )

        captured = record_payment_captured(token)
        if not captured:
            raise HTTPException(
                status_code=500,
                detail=f"Payment captured via PayPal Sandbox, but database record for order '{token}' failed to update.",
            )
    except HTTPException:
        raise
    except PayPalError as exc:
        logger.error("PayPal capture error: %s", exc)
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Unexpected error capturing payment: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    with _lock:
        if s:
            s["status"] = "captured"
            s["events"].append({
                "step": len(s["events"]) + 1,
                "agent": "System",
                "message": "Payment captured. Transaction complete and settled via PayPal Sandbox.",
                "status": "captured",
                "event_type": "payment_captured",
            })
    if target_session_id:
        record_session_event(
            session_id=target_session_id,
            agent="System",
            event_type="payment_captured",
            message="Payment captured via PayPal Sandbox.",
            status="captured",
        )
    return {"status": "captured", "order_id": token}


# ---------------------------------------------------------------------------
# Buyer Profile, Preferences & Behavioral Learning Endpoints
# ---------------------------------------------------------------------------
@app.get("/api/buyer/profile")
async def get_profile(buyer_id: str = "default_buyer"):
    """Fetch persistent buyer profile and active preferences."""
    profile = get_buyer_profile(buyer_id)
    saved_prefs = get_saved_buyer_preferences(buyer_id)
    effective_prefs = resolve_effective_preferences(buyer_id=buyer_id)
    return {
        "profile": profile,
        "saved_preferences": saved_prefs.model_dump(),
        "effective_preferences": effective_prefs.model_dump(),
    }


@app.post("/api/buyer/preferences")
async def update_preferences(body: BuyerPreferences, buyer_id: str = "default_buyer"):
    """Explicitly save buyer preferences (Requirement 4.1)."""
    saved = save_buyer_preferences(buyer_id=buyer_id, preferences=body, source="explicit")
    return {"status": "saved", "preferences": saved.model_dump()}


@app.get("/api/buyer/recommendations")
async def get_recommendations(buyer_id: str = "default_buyer"):
    """Fetch pending learned recommendations awaiting buyer consent (Requirement 4.4)."""
    return get_pending_recommendations(buyer_id=buyer_id)


@app.post("/api/buyer/recommendations/{recommendation_id}/accept")
async def accept_rec(recommendation_id: str, buyer_id: str = "default_buyer"):
    """Buyer explicitly accepts an AI recommendation, activating learned preferences (Requirement 4.4)."""
    try:
        updated_prefs = accept_recommendation(recommendation_id=recommendation_id, buyer_id=buyer_id)
        return {"status": "accepted", "preferences": updated_prefs.model_dump()}
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@app.post("/api/buyer/recommendations/{recommendation_id}/ignore")
async def ignore_rec(recommendation_id: str, buyer_id: str = "default_buyer"):
    """Buyer explicitly declines an AI recommendation (Requirement 4.4)."""
    ignore_recommendation(recommendation_id=recommendation_id, buyer_id=buyer_id)
    return {"status": "ignored", "id": recommendation_id}


# ---------------------------------------------------------------------------
# Procurement Intelligence Dashboard & Analytics
# ---------------------------------------------------------------------------
@app.get("/api/analytics")
async def get_analytics():
    """Returns analytics for procurement dashboard (spend, supplier reliability, strategy win rates)."""
    overall = get_overall_procurement_analytics()
    supplier_metrics = {
        name: get_supplier_metrics(name)
        for name in [info["name"] for info in SELLER_PERSONAS.values()]
    }
    strategy_metrics = get_strategy_metrics()
    return {
        "overall": overall,
        "suppliers": supplier_metrics,
        "strategies": strategy_metrics,
    }


# ---------------------------------------------------------------------------
# What-If Procurement Scenario Simulator
# ---------------------------------------------------------------------------
@app.post("/api/simulator")
async def simulate_scenario(body: WhatIfSimulationRequest):
    """Run what-if procurement simulation across the 3 seller personas (Requirement 9)."""
    return run_what_if_simulation(body)


# ---------------------------------------------------------------------------
# Quote & Contract Comparison Engine
# ---------------------------------------------------------------------------
@app.post("/api/quotes/compare")
async def compare_vendor_quotes(body: QuoteComparisonRequest):
    """Side-by-side comparison of supplier quotes against constraints and priorities (Requirement 14)."""
    return compare_quotes(body)


# ---------------------------------------------------------------------------
# Decision Audit Trail
# ---------------------------------------------------------------------------
@app.get("/api/audit-trail")
async def get_audit_trail(session_id: str = Query(..., description="Procurement session ID")):
    """Returns governance audit trail of autonomous agent decisions for a session."""
    trail = get_session_audit_trail(session_id)
    return {"session_id": session_id, "audit_trail": trail}


# ---------------------------------------------------------------------------
# Health Check Endpoint
# ---------------------------------------------------------------------------
@app.get("/api/health")
async def health():
    """Health check endpoint for deployment monitoring."""
    return {"status": "healthy", "service": "b2b-agents-backend", "version": "2.0.0"}
