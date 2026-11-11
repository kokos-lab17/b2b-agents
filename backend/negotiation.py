"""Autonomous adaptive negotiation engine and deterministic demo simulator (Features 1, 2, 3, 7, 8)."""
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Callable

from backend.config import settings
from backend.groq_client import BudgetExhaustedError, GroqClient
from backend.models import (
    BuyerProposal,
    CommercialOffer,
    CommercialScoreBreakdown,
    Event,
    PrivateSellerConstraint,
    SupplierProfile,
    validate_and_sanitize_offer,
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

        # 2. Buyer Strategy Formulation: Initial strategy based on product & budget
        initial_strategy = "PRICE_DISCOUNT"
        target_discount_unit = round(self.budget * 0.80, 2)
        self._emit(
            agent="Buyer Agent",
            message=(
                f"Formulating initial commercial strategy for {self.qty} units of {self.product}. "
                f"Opening strategy: '{initial_strategy}'. Target unit price: ${target_discount_unit:.2f}/unit."
            ),
            status="negotiating",
            event_type="strategy_switch",
            strategy=initial_strategy,
            offer=target_discount_unit,
        )

        # 3. Round 1: Solicit Initial Offers from the 3 Independent Sellers
        round1_offers: dict[str, CommercialOffer] = {}
        for key, p in self.sellers_info.items():
            offer = self._seller_round1_decision(key, target_discount_unit)
            # Python validation enforcement across all 5 dimensions
            validated_offer = validate_and_sanitize_offer(offer, self.private_constraints[key], self.qty)
            round1_offers[key] = validated_offer

            self._emit(
                agent=p["name"],
                message=(
                    f"[{p['style']}] Evaluated opening inquiry. Counteroffer: ${validated_offer.unit_price:.2f}/unit, "
                    f"lead time {validated_offer.delivery_days} days, warranty {validated_offer.warranty_months} mos, "
                    f"freight ${validated_offer.shipping_cost:.2f}, terms: {validated_offer.payment_terms}."
                ),
                status="negotiating",
                event_type="counteroffer",
                strategy=initial_strategy,
                offer=validated_offer.unit_price,
                details=validated_offer.model_dump(),
            )
            record_negotiation_attempt(
                session_id=self.session_id,
                supplier_name=p["name"],
                rounds=1,
                outcome="countered",
                final_unit_price=validated_offer.unit_price,
                final_total_price=validated_offer.total_price,
            )

        # 4. Genuinely Autonomous Buyer Evaluation & Concrete Proposals (Feature 1 & 2)
        # Buyer dynamically evaluates the actual seller responses and decides whether to continue
        # with price or adaptively switch strategies based on seller pushback.
        buyer_decision = self._buyer_evaluate_and_decide(round1_offers)
        current_strategy = buyer_decision["chosen_strategy"]
        proposals_by_seller = buyer_decision["proposals"]

        self._emit(
            agent="Buyer Agent",
            message=f"Autonomous State Analysis: {buyer_decision['strategy_reasoning']}",
            status="negotiating",
            event_type="strategy_switch",
            strategy=current_strategy,
        )

        # 5. Round 2: Sellers Evaluate Buyer's Concrete Structured Proposals
        final_offers: dict[str, CommercialOffer] = {}
        for key, p in self.sellers_info.items():
            r1 = round1_offers[key]
            prop = proposals_by_seller.get(key)
            offer_r2 = self._seller_round2_decision(key, r1, prop, current_strategy)
            # Python validation enforcement across all 5 dimensions
            validated_r2 = validate_and_sanitize_offer(offer_r2, self.private_constraints[key], self.qty)
            final_offers[key] = validated_r2

            concession_summary = ", ".join(validated_r2.special_concessions) if validated_r2.special_concessions else "terms adjusted"
            self._emit(
                agent=p["name"],
                message=(
                    f"[{p['style']}] Evaluated buyer proposal ({prop.rationale if prop else current_strategy}). "
                    f"Decision: {concession_summary}. "
                    f"Final terms: ${validated_r2.unit_price:.2f}/unit, "
                    f"warranty {validated_r2.warranty_months} mos, lead time {validated_r2.delivery_days} days, "
                    f"shipping ${validated_r2.shipping_cost:.2f}, terms: {validated_r2.payment_terms}."
                ),
                status="negotiating",
                event_type="counteroffer",
                strategy=current_strategy,
                offer=validated_r2.unit_price,
                details=validated_r2.model_dump(),
            )
            record_negotiation_attempt(
                session_id=self.session_id,
                supplier_name=p["name"],
                rounds=2,
                outcome="final_terms_proposed",
                final_unit_price=validated_r2.unit_price,
                final_total_price=validated_r2.total_price,
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
        if self.demo_mode:
            # Deterministic Demo Mode: Register active demo transaction awaiting confirmation
            order_id = f"DEMO-{uuid.uuid4().hex[:12].upper()}"
            record_deal_accepted(
                session_id=self.session_id,
                order_id=order_id,
                supplier_name=winning_name,
                amount=winning_offer.total_price,
            )
            self._emit(
                agent="System",
                message=(
                    f"SIMULATED DEAL APPROVED: Order {order_id} awaiting approval. "
                    f"Deal locked at ${winning_offer.unit_price:.2f}/unit (${winning_offer.total_price:.2f} total). "
                    "Demo mode demonstrates autonomous multi-agent strategy switching without real PayPal transactions."
                ),
                status="awaiting_approval",
                event_type="order_created",
                offer=winning_offer.unit_price,
            )
            record_session(
                session_id=self.session_id,
                product=self.product,
                qty=self.qty,
                budget=self.budget,
                status="awaiting_approval",
                is_demo=True,
            )
            return {
                "session_id": self.session_id,
                "status": "awaiting_approval",
                "order_id": order_id,
                "approve_url": "https://www.sandbox.paypal.com/demo-approval",
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
    # Buyer Agent: Genuinely Independent Evaluation & Strategy Selection
    # -------------------------------------------------------------------------
    def _buyer_evaluate_and_decide(self, r1_offers: dict[str, CommercialOffer]) -> dict[str, Any]:
        """Buyer Agent evaluates actual Round 1 responses and decides next strategy."""
        # 1. Live Groq LLM Evaluation
        if not self.demo_mode and self.groq_client:
            try:
                system_prompt = (
                    f"{BUYER_PERSONA['psychology']}\n"
                    "You are evaluating actual Round 1 seller responses. Do NOT follow a fixed sequence.\n"
                    "If sellers showed price flexibility, continue pushing for price discounts.\n"
                    "If sellers resisted price cuts, pivot to non-price variables (warranty, freight, payment terms).\n"
                    "Respond ONLY with valid JSON matching this schema:\n"
                    "{\n"
                    '  "chosen_strategy": "PRICE_DISCOUNT" | "WARRANTY_EXPANSION_AND_FREIGHT_CONCESSION" | "EXPEDITED_DELIVERY_AND_TERMS",\n'
                    '  "strategy_reasoning": "Explain why this strategy was chosen based on the seller offers",\n'
                    '  "proposals": {\n'
                    '     "seller_a": {"target_unit_price": float, "target_delivery_days": int, "target_warranty_months": int, "target_shipping_cost": float, "target_payment_terms": str, "rationale": str, "concession_requested": str},\n'
                    '     "seller_b": {"target_unit_price": float, "target_delivery_days": int, "target_warranty_months": int, "target_shipping_cost": float, "target_payment_terms": str, "rationale": str, "concession_requested": str},\n'
                    '     "seller_c": {"target_unit_price": float, "target_delivery_days": int, "target_warranty_months": int, "target_shipping_cost": float, "target_payment_terms": str, "rationale": str, "concession_requested": str}\n'
                    "  }\n"
                    "}"
                )
                r1_summary = {
                    key: {
                        "vendor": self.sellers_info[key]["name"],
                        "unit_price": o.unit_price,
                        "lead_days": o.delivery_days,
                        "warranty_mos": o.warranty_months,
                        "shipping": o.shipping_cost,
                        "terms": o.payment_terms,
                    }
                    for key, o in r1_offers.items()
                }
                user_msg = (
                    f"Product: {self.product}, Qty: {self.qty}, Budget: ${self.budget:.2f}/unit.\n"
                    f"Actual Seller Round 1 Offers: {json.dumps(r1_summary)}\n"
                    "Evaluate these offers. Choose the most effective strategy and formulate concrete proposals for each seller."
                )
                data = self.groq_client.call_agent(
                    agent_name="Buyer Agent",
                    system_prompt=system_prompt,
                    user_message=user_msg,
                )
                if isinstance(data, dict) and "chosen_strategy" in data and "proposals" in data:
                    parsed_proposals = {}
                    for key, prop_dict in data.get("proposals", {}).items():
                        if key in self.sellers_info and isinstance(prop_dict, dict):
                            parsed_proposals[key] = BuyerProposal(
                                target_unit_price=float(prop_dict.get("target_unit_price", self.budget * 0.85)),
                                target_delivery_days=int(prop_dict.get("target_delivery_days", 10)),
                                target_warranty_months=int(prop_dict.get("target_warranty_months", 12)),
                                target_shipping_cost=float(prop_dict.get("target_shipping_cost", 0.0)),
                                target_payment_terms=str(prop_dict.get("target_payment_terms", "Net 30")),
                                rationale=str(prop_dict.get("rationale", "")),
                                concession_requested=str(prop_dict.get("concession_requested", "")),
                            )
                    return {
                        "chosen_strategy": str(data["chosen_strategy"]),
                        "strategy_reasoning": str(data.get("strategy_reasoning", "Pivoting strategy based on commercial state.")),
                        "proposals": parsed_proposals,
                    }
            except Exception as exc:
                logger.warning("Groq call failed for Buyer Agent, falling back to state-dependent policy: %s", exc)

        # 2. State-Dependent Decision Policy (Used in Demo Mode or Fallback)
        # Evaluates actual numerical positions of the sellers:
        min_offered_price = min(o.unit_price for o in r1_offers.values())
        discount_percentage = (self.budget - min_offered_price) / self.budget

        # If sellers offered >= 12% discount below budget in Round 1:
        # Sellers demonstrated price flexibility! Buyer pushes further on price.
        if discount_percentage >= 0.12:
            chosen_strategy = "PRICE_DISCOUNT"
            reasoning = (
                f"Sellers demonstrated notable price flexibility in Round 1 (lowest offer ${min_offered_price:.2f}/unit "
                f"is {discount_percentage*100:.1f}% below budget cap). Continuing aggressive price negotiation."
            )
            proposals = {
                "seller_a": BuyerProposal(
                    target_unit_price=round(r1_offers["seller_a"].unit_price * 0.95, 2),
                    target_delivery_days=r1_offers["seller_a"].delivery_days,
                    target_warranty_months=12,
                    target_shipping_cost=r1_offers["seller_a"].shipping_cost,
                    target_payment_terms="Net 30",
                    rationale="Offering volume commitment in exchange for additional 5% unit price reduction.",
                    concession_requested="Volume discount on unit price",
                ),
                "seller_b": BuyerProposal(
                    target_unit_price=round(r1_offers["seller_b"].unit_price * 0.96, 2),
                    target_delivery_days=r1_offers["seller_b"].delivery_days,
                    target_warranty_months=18,
                    target_shipping_cost=r1_offers["seller_b"].shipping_cost,
                    target_payment_terms="Net 30",
                    rationale="Requesting unit price concession matching market competitive pricing.",
                    concession_requested="Direct price match",
                ),
                "seller_c": BuyerProposal(
                    target_unit_price=round(r1_offers["seller_c"].unit_price * 0.95, 2),
                    target_delivery_days=7,
                    target_warranty_months=12,
                    target_shipping_cost=r1_offers["seller_c"].shipping_cost,
                    target_payment_terms="Net 30",
                    rationale="Pushing for additional unit savings on standard lead times.",
                    concession_requested="Production efficiency discount",
                ),
            }
        else:
            # Sellers showed firm resistance to price cuts (<12% discount).
            # Buyer autonomously shifts strategy to capture commercial value from non-price variables.
            chosen_strategy = "WARRANTY_EXPANSION_AND_FREIGHT_CONCESSION"
            reasoning = (
                f"Sellers showed firm resistance to unit price cuts (lowest offer ${min_offered_price:.2f}/unit "
                f"is only {discount_percentage*100:.1f}% below budget). Diminishing returns on further price pressure. "
                "Pivoting strategy from 'PRICE_DISCOUNT' to 'WARRANTY_EXPANSION_AND_FREIGHT_CONCESSION' to capture "
                "non-price commercial value via extended warranty (24+ mos), waived freight, and Net 60 payment terms."
            )
            proposals = {
                "seller_a": BuyerProposal(
                    target_unit_price=r1_offers["seller_a"].unit_price,
                    target_delivery_days=10,
                    target_warranty_months=12,
                    target_shipping_cost=0.0,
                    target_payment_terms="Net 30",
                    rationale="Accepting your unit price if freight shipping is absorbed and volume commitment is noted.",
                    concession_requested="Waived freight shipping",
                ),
                "seller_b": BuyerProposal(
                    target_unit_price=r1_offers["seller_b"].unit_price,
                    target_delivery_days=12,
                    target_warranty_months=24,
                    target_shipping_cost=0.0,
                    target_payment_terms="Net 30",
                    rationale="Respecting engineering value by holding unit price in exchange for 24-month warranty and free freight.",
                    concession_requested="Extended 24-month warranty and free shipping",
                ),
                "seller_c": BuyerProposal(
                    target_unit_price=round(r1_offers["seller_c"].unit_price - 1.0, 2),
                    target_delivery_days=5,
                    target_warranty_months=12,
                    target_shipping_cost=0.0,
                    target_payment_terms="Net 60",
                    rationale="Requesting expedited 5-day dispatch, Net 60 payment terms, and free freight.",
                    concession_requested="Expedited dispatch and Net 60 terms",
                ),
            }

        return {
            "chosen_strategy": chosen_strategy,
            "strategy_reasoning": reasoning,
            "proposals": proposals,
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
                    shipping = float(data.get("shipping_cost", c.min_shipping_cost))
                    delivery = int(data.get("delivery_days", 14))
                    warranty = int(data.get("warranty_months", 12))
                    terms = str(data.get("payment_terms", c.allowed_payment_terms[0]))
                    raw_offer = CommercialOffer(
                        unit_price=proposed_price,
                        total_price=round(proposed_price * self.qty + shipping, 2),
                        quantity=self.qty,
                        delivery_days=delivery,
                        warranty_months=warranty,
                        shipping_cost=shipping,
                        payment_terms=terms,
                    )
                    return validate_and_sanitize_offer(raw_offer, c, self.qty)
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
        self,
        seller_key: str,
        r1_offer: CommercialOffer,
        buyer_proposal: BuyerProposal | None,
        strategy: str,
    ) -> CommercialOffer:
        """Round 2: Seller evaluates the concrete structured proposal from the Buyer."""
        c = self.private_constraints[seller_key]
        p = self.sellers_info[seller_key]

        if not self.demo_mode and self.groq_client and buyer_proposal:
            try:
                system_prompt = (
                    f"{p['psychology']} Respond ONLY with valid JSON.\n"
                    f"Your private constraints: min_price=${c.min_unit_price:.2f}, min_delivery={c.min_delivery_days}d, "
                    f"max_warranty={c.max_warranty_months}m, min_shipping=${c.min_shipping_cost:.2f}, allowed_terms={c.allowed_payment_terms}.\n"
                    "Evaluate the buyer's concrete proposal. Make concessions ONLY where it aligns with your psychology.\n"
                    "Output schema: {{\"unit_price\": float, \"delivery_days\": int, \"warranty_months\": int, \"shipping_cost\": float, \"payment_terms\": str, \"special_concessions\": [str]}}"
                )
                user_msg = (
                    f"Your Round 1 Offer was: ${r1_offer.unit_price:.2f}/unit, {r1_offer.delivery_days} days, {r1_offer.warranty_months} mos warranty, ${r1_offer.shipping_cost:.2f} shipping, terms: {r1_offer.payment_terms}.\n"
                    f"Buyer Proposal for you: target_price=${buyer_proposal.target_unit_price:.2f}, target_delivery={buyer_proposal.target_delivery_days} days, "
                    f"target_warranty={buyer_proposal.target_warranty_months} mos, target_shipping=${buyer_proposal.target_shipping_cost:.2f}, terms={buyer_proposal.target_payment_terms}.\n"
                    f"Buyer Rationale: {buyer_proposal.rationale}\n"
                    f"Concession Requested: {buyer_proposal.concession_requested}\n"
                    "Provide your final counter-offer."
                )
                data = self.groq_client.call_agent(
                    agent_name=p["name"],
                    system_prompt=system_prompt,
                    user_message=user_msg,
                )
                if isinstance(data, dict):
                    proposed_price = float(data.get("unit_price", r1_offer.unit_price))
                    shipping = float(data.get("shipping_cost", 0.0))
                    delivery = int(data.get("delivery_days", r1_offer.delivery_days))
                    warranty = int(data.get("warranty_months", r1_offer.warranty_months))
                    terms = str(data.get("payment_terms", "Net 30"))
                    concessions = list(data.get("special_concessions", []))
                    raw_offer = CommercialOffer(
                        unit_price=proposed_price,
                        total_price=round(proposed_price * self.qty + shipping, 2),
                        quantity=self.qty,
                        delivery_days=delivery,
                        warranty_months=warranty,
                        shipping_cost=shipping,
                        payment_terms=terms,
                        special_concessions=concessions,
                    )
                    return validate_and_sanitize_offer(raw_offer, c, self.qty)
            except Exception as exc:
                logger.warning("Groq call failed for %s round 2, using deterministic policy: %s", seller_key, exc)

        # Deterministic Policy (Feature 3 & 7): Distinct Psychological Bargaining
        concession_text = buyer_proposal.concession_requested.lower() if buyer_proposal else ""
        rationale_text = buyer_proposal.rationale.lower() if buyer_proposal else ""

        if seller_key == "seller_a":
            # The Hard Bargainer:
            # Genuinely resists! If buyer does NOT offer a volume commitment or valuable concession,
            # Seller A refuses to concede on price!
            has_volume_commitment = "volume" in concession_text or "volume" in rationale_text or "repeat" in rationale_text
            if has_volume_commitment:
                # Softens moderately: provides $1.50 volume discount and lowers shipping to $100
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
            else:
                # Firm refusal: Does NOT concede price without volume commitment!
                return CommercialOffer(
                    unit_price=r1_offer.unit_price,
                    total_price=r1_offer.total_price,
                    quantity=self.qty,
                    delivery_days=r1_offer.delivery_days,
                    warranty_months=r1_offer.warranty_months,
                    shipping_cost=r1_offer.shipping_cost,
                    payment_terms=r1_offer.payment_terms,
                    special_concessions=["Price Firm — Volume Commitment Required for Discounts"],
                )

        elif seller_key == "seller_b":
            # The Value Defender:
            # Resists direct unit price cut, but upgrades warranty to 24 months and absorbs shipping to protect margin
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
            # The Strategic Flexible Negotiator:
            # Flexes on delivery lead time, Net 60 terms, and free shipping
            new_price = round(max(c.min_unit_price, r1_offer.unit_price - 1.00), 2)
            return CommercialOffer(
                unit_price=new_price,
                total_price=round(new_price * self.qty, 2),
                quantity=self.qty,
                delivery_days=5,
                warranty_months=12,
                shipping_cost=0.0,  # Free shipping
                payment_terms="Net 60",
                special_concessions=["Expedited 5-Day Delivery", "Net 60 Terms", "Free Shipping"],
            )
