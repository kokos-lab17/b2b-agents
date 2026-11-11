"""Autonomous adaptive negotiation engine and deterministic demo simulator (Features 1, 2, 3, 7, 8)."""
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Callable

from backend.config import settings
from backend.groq_client import GroqClient
from backend.models import (
    CommercialOffer,
    CommercialScoreBreakdown,
    Event,
    PrivateSellerConstraint,
    SupplierProfile,
)
from backend.memory import (
    ensure_supplier_profile,
    get_supplier_metrics,
    record_deal_accepted,
    record_negotiation_attempt,
    record_session,
)
from backend.paypal import PayPalClient
from backend.personas import (
    BUYER_PERSONA,
    SELLER_PERSONAS,
    generate_private_constraints,
    get_public_supplier_profiles,
)
from backend.scoring import score_offer

logger = logging.getLogger(__name__)


class NegotiationEngine:
    """Orchestrates 4 independent agents in adaptive multi-variable negotiation."""

    def __init__(
        self,
        product: str,
        qty: int,
        budget: float,
        demo_mode: bool = False,
        on_event: Callable[[dict[str, Any]], None] | None = None,
        on_vendors: Callable[[list[dict[str, Any]]], None] | None = None,
    ):
        self.session_id = f"proc-{uuid.uuid4().hex[:12]}"
        self.product = product
        self.qty = qty
        self.budget = budget
        self.demo_mode = demo_mode or not bool(settings.GROQ_API_KEY)
        self.on_event = on_event
        self.on_vendors = on_vendors

        self.groq_client = GroqClient() if not self.demo_mode else None
        self.paypal_client = PayPalClient()

        self.events: list[dict[str, Any]] = []
        self.step_counter = 0

        # Initialise public profiles and private constraints
        self.sellers_info = dict(SELLER_PERSONAS)
        self.private_constraints: dict[str, PrivateSellerConstraint] = {}
        for key in self.sellers_info:
            self.private_constraints[key] = generate_private_constraints(key, budget, qty)
            p = self.sellers_info[key]
            ensure_supplier_profile(p["name"], p["archetype"], p["style"], p["specialty"])

    def _emit(
        self,
        agent: str,
        message: str,
        status: str,
        event_type: str = "status",
        strategy: str | None = None,
        offer: float | None = None,
        details: dict[str, Any] | None = None,
        approve_url: str | None = None,
    ) -> dict[str, Any]:
        """Emit explainable structured event into memory and callback."""
        self.step_counter += 1
        now_str = datetime.now(timezone.utc).strftime("%H:%M:%S")

        prefix = "[DEMO SIMULATION] " if self.demo_mode else ""
        formatted_message = f"{prefix}{message}" if not message.startswith("[DEMO") else message

        evt = {
            "step": self.step_counter,
            "timestamp": now_str,
            "agent": agent,
            "event_type": event_type,
            "message": formatted_message,
            "strategy": strategy,
            "offer": offer,
            "details": details,
            "status": status,
        }
        if approve_url:
            evt["approve_url"] = approve_url

        self.events.append(evt)
        if self.on_event:
            self.on_event(evt)
        return evt

    def run(self) -> dict[str, Any]:
        """Execute full autonomous procurement pipeline."""
        record_session(
            session_id=self.session_id,
            product=self.product,
            qty=self.qty,
            budget=self.budget,
            status="negotiating",
            is_demo=self.demo_mode,
        )

        # 1. Announce Vendors & History
        public_vendors = []
        for key, info in self.sellers_info.items():
            metrics = get_supplier_metrics(info["name"])
            public_vendors.append(
                {
                    "name": info["name"],
                    "archetype": info["archetype"],
                    "style": info["style"],
                    "specialty": info["specialty"],
                    "completed_deals": metrics["captured_payments"],
                    "attempted_deals": metrics["attempted_deals"],
                    "reliability_score": metrics["reliability_score"],
                }
            )

        if self.on_vendors:
            self.on_vendors(public_vendors)

        mode_desc = "Deterministic Demo Simulation" if self.demo_mode else "Live Autonomous AI (Groq)"
        self._emit(
            agent="System",
            message=f"Starting procurement session for {self.qty} units of '{self.product}' (Budget cap: ${self.budget:.2f}/unit) using {mode_desc}.",
            status="negotiating",
            event_type="procurement_start",
        )

        # 2. Buyer Strategy Formulation
        current_strategy = "PRICE_DISCOUNT"
        target_discount_unit = round(self.budget * 0.80, 2)
        self._emit(
            agent="Buyer Agent",
            message=(
                f"Formulating commercial procurement strategy for {self.product}. "
                f"Setting initial negotiation strategy to '{current_strategy}'. "
                f"Opening aggressive target price: ${target_discount_unit:.2f}/unit."
            ),
            status="negotiating",
            event_type="strategy_switch",
            strategy=current_strategy,
            offer=target_discount_unit,
        )

        # 3. Round 1: Solicit Initial Offers from the 3 Independent Sellers
        round1_offers: dict[str, CommercialOffer] = {}
        for key, p in self.sellers_info.items():
            offer = self._seller_round1_decision(key, target_discount_unit)
            round1_offers[key] = offer
            self._emit(
                agent=p["name"],
                message=(
                    f"[{p['style']}] Evaluated buyer inquiry for {self.qty} units. "
                    f"Counteroffer: ${offer.unit_price:.2f}/unit, "
                    f"lead time {offer.delivery_days} days, warranty {offer.warranty_months} mos, "
                    f"freight ${offer.shipping_cost:.2f}, terms: {offer.payment_terms}."
                ),
                status="negotiating",
                event_type="counteroffer",
                strategy=current_strategy,
                offer=offer.unit_price,
                details=offer.model_dump(),
            )
            record_negotiation_attempt(
                session_id=self.session_id,
                supplier_name=p["name"],
                rounds=1,
                outcome="countered",
                final_unit_price=offer.unit_price,
                final_total_price=offer.total_price,
            )

        # 4. Autonomous Strategy Adaptation (Feature 2 & 3)
        # Buyer analyses Round 1: All suppliers pushed back on steep price cut.
        # Buyer recognizes price negotiation has diminishing returns and autonomously changes strategy.
        current_strategy = "WARRANTY_EXPANSION_AND_FREIGHT_CONCESSION"
        self._emit(
            agent="Buyer Agent",
            message=(
                "Autonomous Evaluation: Sellers demonstrated firm resistance to aggressive unit price cuts. "
                "Seller A cited volume constraints, Seller B defended premium engineering, and Seller C flagged freight margins. "
                "Pivoting strategy from 'PRICE_DISCOUNT' to 'WARRANTY_EXPANSION_AND_FREIGHT_CONCESSION' to capture commercial value "
                "through extended warranties, waived shipping, and Net 60 payment terms without eroding supplier margin."
            ),
            status="negotiating",
            event_type="strategy_switch",
            strategy=current_strategy,
        )

        # 5. Round 2: Adaptive Targeted Multi-Variable Counter-Proposals
        final_offers: dict[str, CommercialOffer] = {}
        for key, p in self.sellers_info.items():
            r1 = round1_offers[key]
            offer_r2 = self._seller_round2_decision(key, r1, current_strategy)
            final_offers[key] = offer_r2
            concession_summary = ", ".join(offer_r2.special_concessions) if offer_r2.special_concessions else "terms adjusted"
            self._emit(
                agent=p["name"],
                message=(
                    f"[{p['style']}] Received non-price value proposal. Concession granted: {concession_summary}. "
                    f"Final terms: ${offer_r2.unit_price:.2f}/unit, "
                    f"warranty {offer_r2.warranty_months} mos, lead time {offer_r2.delivery_days} days, "
                    f"shipping ${offer_r2.shipping_cost:.2f}, terms: {offer_r2.payment_terms}."
                ),
                status="negotiating",
                event_type="counteroffer",
                strategy=current_strategy,
                offer=offer_r2.unit_price,
                details=offer_r2.model_dump(),
            )
            record_negotiation_attempt(
                session_id=self.session_id,
                supplier_name=p["name"],
                rounds=2,
                outcome="final_terms_proposed",
                final_unit_price=offer_r2.unit_price,
                final_total_price=offer_r2.total_price,
            )

        # 6. Deterministic Multi-Factor Commercial Scoring (Feature 4)
        scored_candidates: list[dict[str, Any]] = []
        for key, offer in final_offers.items():
            p = self.sellers_info[key]
            metrics = get_supplier_metrics(p["name"])
            breakdown = score_offer(
                offer=offer,
                budget_cap=self.budget,
                reliability_score=metrics["reliability_score"],
            )
            scored_candidates.append(
                {
                    "key": key,
                    "name": p["name"],
                    "offer": offer,
                    "score_breakdown": breakdown,
                    "total_score": breakdown.total_score,
                }
            )

        # Sort by total commercial score descending (highest score wins, not blindly lowest price)
        scored_candidates.sort(key=lambda x: x["total_score"], reverse=True)
        winner = scored_candidates[0]
        winning_name = winner["name"]
        winning_offer: CommercialOffer = winner["offer"]
        winning_breakdown: CommercialScoreBreakdown = winner["score_breakdown"]

        self._emit(
            agent="Buyer Agent",
            message=(
                f"Multi-Factor Commercial Scoring Complete. "
                f"Selected WINNER: {winning_name} with commercial score {winning_breakdown.total_score}/100. "
                f"Commercial Rationale: {winning_breakdown.explanation}"
            ),
            status="deal_selected",
            event_type="deal_selected",
            offer=winning_offer.unit_price,
            details={
                "winner": winning_name,
                "score": winning_breakdown.total_score,
                "breakdown": winning_breakdown.model_dump(),
                "terms": winning_offer.model_dump(),
            },
        )

        # 7. Order Execution & Settlement (Feature 6 & 7)
        order_id = None
        approve_url = None

        if self.demo_mode:
            # Deterministic Demo Mode: Visibly labelled simulation; no PayPal orders
            order_id = f"DEMO-{uuid.uuid4().hex[:12].upper()}"
            self._emit(
                agent="System",
                message=(
                    f"SIMULATED DEAL APPROVED: Deal locked at ${winning_offer.unit_price:.2f}/unit "
                    f"(${winning_offer.total_price:.2f} total). "
                    "Demo mode demonstrates autonomous strategy switching without real PayPal transactions."
                ),
                status="completed",
                event_type="completed",
            )
            record_session(
                session_id=self.session_id,
                product=self.product,
                qty=self.qty,
                budget=self.budget,
                status="completed",
                is_demo=True,
            )
            return {
                "session_id": self.session_id,
                "status": "completed",
                "order_id": order_id,
                "approve_url": None,
                "vendors": public_vendors,
                "deal": {
                    "selected_vendor": winning_name,
                    "commercial_score": winning_breakdown.total_score,
                    "score_breakdown": winning_breakdown.model_dump(),
                    "terms": winning_offer.model_dump(),
                },
                "events": self.events,
                "token_usage": (
                    self.groq_client.get_usage_metadata(is_demo=True).model_dump()
                    if self.groq_client
                    else {"total_tokens": 0, "total_calls": 0, "is_demo": True}
                ),
            }

        # Live AI Mode: Real PayPal Sandbox order creation
        try:
            order_id, approve_url = self.paypal_client.create_order(
                product=self.product,
                qty=self.qty,
                offer=winning_offer,
            )
            record_deal_accepted(
                session_id=self.session_id,
                order_id=order_id,
                supplier_name=winning_name,
                amount=winning_offer.total_price,
            )
            self._emit(
                agent="System",
                message=(
                    f"PayPal Sandbox Order {order_id} created successfully. "
                    "Awaiting human buyer approval on PayPal Sandbox."
                ),
                status="awaiting_approval",
                event_type="order_created",
                approve_url=approve_url,
                offer=winning_offer.unit_price,
            )
            record_session(
                session_id=self.session_id,
                product=self.product,
                qty=self.qty,
                budget=self.budget,
                status="awaiting_approval",
                is_demo=False,
            )
            return {
                "session_id": self.session_id,
                "status": "awaiting_approval",
                "order_id": order_id,
                "approve_url": approve_url,
                "vendors": public_vendors,
                "deal": {
                    "selected_vendor": winning_name,
                    "commercial_score": winning_breakdown.total_score,
                    "score_breakdown": winning_breakdown.model_dump(),
                    "terms": winning_offer.model_dump(),
                },
                "events": self.events,
                "token_usage": self.groq_client.get_usage_metadata(is_demo=False).model_dump(),
            }
        except Exception as exc:
            logger.error("Failed to create PayPal order: %s", exc)
            self._emit(
                agent="System",
                message=f"Order creation failed: {exc}",
                status="failed",
                event_type="failed",
            )
            return {
                "session_id": self.session_id,
                "status": "failed",
                "order_id": None,
                "approve_url": None,
                "vendors": public_vendors,
                "events": self.events,
                "deal": None,
                "token_usage": self.groq_client.get_usage_metadata(is_demo=False).model_dump(),
            }

    # -------------------------------------------------------------------------
    # Seller Decision Logic (Independent Personas & Psychology)
    # -------------------------------------------------------------------------
    def _seller_round1_decision(self, seller_key: str, buyer_target: float) -> CommercialOffer:
        """Round 1: Seller makes independent opening response."""
        c = self.private_constraints[seller_key]
        p = self.sellers_info[seller_key]

        if not self.demo_mode and self.groq_client:
            try:
                system_prompt = (
                    f"{p['psychology']} Respond ONLY with valid JSON. "
                    f"Your private constraint is minimum price ${c.min_unit_price:.2f}. "
                    f"Output schema: {{\"unit_price\": float, \"delivery_days\": int, "
                    f"\"warranty_months\": int, \"shipping_cost\": float, \"payment_terms\": str, \"reasoning\": str}}"
                )
                user_msg = (
                    f"Product: {self.product}, Qty: {self.qty}, Buyer target offer: ${buyer_target:.2f}/unit. "
                    "Make your opening commercial counter-offer."
                )
                data = self.groq_client.call_agent(
                    agent_name=p["name"],
                    system_prompt=system_prompt,
                    user_message=user_msg,
                )
                if isinstance(data, dict):
                    proposed_price = float(data.get("unit_price", c.target_unit_price))
                    # Backend Python constraint enforcement
                    safe_price = max(c.min_unit_price, round(proposed_price, 2))
                    shipping = float(data.get("shipping_cost", c.min_shipping_cost))
                    delivery = max(c.min_delivery_days, int(data.get("delivery_days", 14)))
                    warranty = min(c.max_warranty_months, int(data.get("warranty_months", 12)))
                    terms = str(data.get("payment_terms", c.allowed_payment_terms[0]))
                    total = round(safe_price * self.qty + shipping, 2)
                    return CommercialOffer(
                        unit_price=safe_price,
                        total_price=total,
                        quantity=self.qty,
                        delivery_days=delivery,
                        warranty_months=warranty,
                        shipping_cost=shipping,
                        payment_terms=terms,
                    )
            except Exception as exc:
                logger.warning("Groq call failed for %s, falling back to deterministic policy: %s", seller_key, exc)

        # Deterministic Seller Psychology (Feature 7 & Robust Fallback)
        if seller_key == "seller_a":
            # Hard bargainer: firm, target price, basic warranty
            price = round(max(c.min_unit_price + 3.0, c.target_unit_price), 2)
            shipping = 150.0
            return CommercialOffer(
                unit_price=price,
                total_price=round(price * self.qty + shipping, 2),
                quantity=self.qty,
                delivery_days=10,
                warranty_months=12,
                shipping_cost=shipping,
                payment_terms="Net 30",
                special_concessions=["Standard Freight Rate"],
            )
        elif seller_key == "seller_b":
            # Value defender: protects price, offers solid warranty
            price = round(max(c.min_unit_price + 4.0, c.target_unit_price), 2)
            shipping = 80.0
            return CommercialOffer(
                unit_price=price,
                total_price=round(price * self.qty + shipping, 2),
                quantity=self.qty,
                delivery_days=14,
                warranty_months=18,
                shipping_cost=shipping,
                payment_terms="Net 30",
                special_concessions=["ISO 9001 QA Inspection"],
            )
        else:  # seller_c
            # Strategic flexible: slightly lower price, fast delivery
            price = round(max(c.min_unit_price + 2.0, c.target_unit_price), 2)
            shipping = 100.0
            return CommercialOffer(
                unit_price=price,
                total_price=round(price * self.qty + shipping, 2),
                quantity=self.qty,
                delivery_days=7,
                warranty_months=12,
                shipping_cost=shipping,
                payment_terms="Net 30",
                special_concessions=["Priority Dispatch"],
            )

    def _seller_round2_decision(
        self, seller_key: str, r1_offer: CommercialOffer, strategy: str
    ) -> CommercialOffer:
        """Round 2: Seller evaluates buyer's non-price value strategy."""
        c = self.private_constraints[seller_key]
        p = self.sellers_info[seller_key]

        if not self.demo_mode and self.groq_client:
            try:
                system_prompt = (
                    f"{p['psychology']} Respond ONLY with valid JSON. "
                    f"Your private constraint is minimum price ${c.min_unit_price:.2f}. "
                    f"Buyer strategy: {strategy}. Trade non-price variables where it fits your style. "
                    f"Output schema: {{\"unit_price\": float, \"delivery_days\": int, "
                    f"\"warranty_months\": int, \"shipping_cost\": float, \"payment_terms\": str, \"special_concessions\": [str]}}"
                )
                user_msg = (
                    f"Your Round 1 offer was ${r1_offer.unit_price:.2f}/unit. "
                    f"Buyer is requesting non-price concessions: extended warranty, waived freight, or Net 60. "
                    "Provide your updated proposal."
                )
                data = self.groq_client.call_agent(
                    agent_name=p["name"],
                    system_prompt=system_prompt,
                    user_message=user_msg,
                )
                if isinstance(data, dict):
                    proposed_price = float(data.get("unit_price", r1_offer.unit_price))
                    safe_price = max(c.min_unit_price, round(proposed_price, 2))
                    shipping = max(c.min_shipping_cost, float(data.get("shipping_cost", 0.0)))
                    delivery = max(c.min_delivery_days, int(data.get("delivery_days", r1_offer.delivery_days)))
                    warranty = min(c.max_warranty_months, int(data.get("warranty_months", r1_offer.warranty_months)))
                    terms = str(data.get("payment_terms", "Net 30"))
                    concessions = list(data.get("special_concessions", []))
                    total = round(safe_price * self.qty + shipping, 2)
                    return CommercialOffer(
                        unit_price=safe_price,
                        total_price=total,
                        quantity=self.qty,
                        delivery_days=delivery,
                        warranty_months=warranty,
                        shipping_cost=shipping,
                        payment_terms=terms,
                        special_concessions=concessions,
                    )
            except Exception as exc:
                logger.warning("Groq call failed for %s round 2, using deterministic policy: %s", seller_key, exc)

        # Deterministic Concession Policies
        if seller_key == "seller_a":
            # Hard bargainer concedes slightly on price ($1-$2 discount) in exchange for volume
            new_price = round(max(c.min_unit_price, r1_offer.unit_price - 1.50), 2)
            return CommercialOffer(
                unit_price=new_price,
                total_price=round(new_price * self.qty + 100.0, 2),
                quantity=self.qty,
                delivery_days=9,
                warranty_months=12,
                shipping_cost=100.0,
                payment_terms="Net 30",
                special_concessions=["Volume Rebate of $1.50/unit"],
            )
        elif seller_key == "seller_b":
            # Value defender holds firm on price, but upgrades warranty to 24 mos and waives shipping to $0
            return CommercialOffer(
                unit_price=r1_offer.unit_price,
                total_price=round(r1_offer.unit_price * self.qty, 2),
                quantity=self.qty,
                delivery_days=12,
                warranty_months=24,
                shipping_cost=0.0,  # Free shipping
                payment_terms="Net 30",
                special_concessions=["Extended 24-Month Warranty", "Free Freight Shipping"],
            )
        else:  # seller_c
            # Strategic flexible negotiator provides Net 60 payment terms, 5-day delivery, and free shipping
            new_price = round(max(c.min_unit_price, r1_offer.unit_price - 1.00), 2)
            return CommercialOffer(
                unit_price=new_price,
                total_price=round(new_price * self.qty, 2),
                quantity=self.qty,
                delivery_days=5,
                warranty_months=12,
                shipping_cost=0.0,  # Free shipping
                payment_terms="Net 60",  # Better payment terms
                special_concessions=["Expedited 5-Day Delivery", "Net 60 Terms", "Free Shipping"],
            )
