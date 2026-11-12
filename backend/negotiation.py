"""Autonomous adaptive negotiation engine and deterministic demo simulator (Features 1, 2, 3, 7, 8)."""
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Callable

from backend.config import settings
from backend.groq_client import BudgetExhaustedError, GroqClient
from backend.models import (
    BuyerPreferences,
    BuyerPreferencePriority,
    BuyerProposal,
    CommercialOffer,
    CommercialScoreBreakdown,
    Event,
    NegotiationMode,
    PaymentMode,
    PrivateSellerConstraint,
    SupplierProfile,
    validate_and_sanitize_offer,
)
from backend.memory import (
    ensure_supplier_profile,
    get_supplier_metrics,
    record_audit_event,
    record_deal_accepted,
    record_negotiation_attempt,
    record_session,
    record_session_event,
    record_strategy_outcome,
)
from backend.paypal import PayPalClient, PayPalError
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
        negotiation_mode: NegotiationMode | str | None = None,
        payment_mode: PaymentMode | str | None = None,
        buyer_id: str = "default_buyer",
        preferences: BuyerPreferences | None = None,
        specifications: str = "",
        session_id: str | None = None,
        on_event: Callable[[dict[str, Any]], None] | None = None,
        on_vendors: Callable[[list[dict[str, Any]]], None] | None = None,
    ):
        self.session_id = session_id or f"proc-{uuid.uuid4().hex[:12]}"
        self.product = product
        self.qty = qty
        self.budget = budget
        self.buyer_id = buyer_id
        self.preferences = preferences
        self.specifications = specifications

        # 1. Negotiation Mode Resolution (DEMO vs LIVE_AI)
        if negotiation_mode is not None:
            self.negotiation_mode = (
                negotiation_mode
                if isinstance(negotiation_mode, NegotiationMode)
                else NegotiationMode(str(negotiation_mode).upper())
            )
        elif demo_mode:
            self.negotiation_mode = NegotiationMode.DEMO
        else:
            self.negotiation_mode = (
                NegotiationMode.DEMO if not bool(settings.GROQ_API_KEY) else NegotiationMode.LIVE_AI
            )

        self.demo_mode = (self.negotiation_mode == NegotiationMode.DEMO)

        # 2. Payment Mode Resolution (SIMULATED vs PAYPAL_SANDBOX)
        if payment_mode is not None:
            self.payment_mode = (
                payment_mode
                if isinstance(payment_mode, PaymentMode)
                else PaymentMode(str(payment_mode).upper())
            )
        elif demo_mode:
            self.payment_mode = PaymentMode.SIMULATED
        else:
            self.payment_mode = PaymentMode.PAYPAL_SANDBOX

        # 3. Groq Client Enforcement
        if self.negotiation_mode == NegotiationMode.LIVE_AI:
            if not bool(settings.GROQ_API_KEY):
                raise ValueError(
                    "Groq API key is not configured for Live AI mode. "
                    "Please set GROQ_API_KEY or select Demo mode."
                )
            self.groq_client = GroqClient()
        else:
            # Deterministic Demo Mode: Zero Groq calls guaranteed
            self.groq_client = None

        self.paypal_client = PayPalClient()
        self.on_event = on_event
        self.on_vendors = on_vendors

        self.events: list[dict[str, Any]] = []
        self.step_counter = 0

        # Initialise public profiles and private constraints (deterministic seeding in demo mode)
        self.sellers_info = dict(SELLER_PERSONAS)
        self.private_constraints: dict[str, PrivateSellerConstraint] = {}

        import hashlib
        pref_key = self.preferences.priority_type.value if self.preferences else "DEFAULT"
        seed_src = f"{self.product}:{self.qty}:{self.budget:.2f}:{self.specifications}:{pref_key}"
        base_seed = int(hashlib.sha256(seed_src.encode("utf-8")).hexdigest()[:8], 16)

        for idx, key in enumerate(self.sellers_info):
            seller_seed = (base_seed + idx * 101) if self.demo_mode else None
            self.private_constraints[key] = generate_private_constraints(key, budget, qty, seed=seller_seed)
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

        # Record event in SQLite database for persistent session isolation
        try:
            record_session_event(
                session_id=self.session_id,
                agent=agent,
                event_type=event_type,
                message=formatted_message,
                status=status,
                strategy=strategy,
                offer=offer,
                details=details,
                approve_url=approve_url,
            )
        except Exception as exc:
            logger.warning("Failed to record session event: %s", exc)

        if self.on_event:
            self.on_event(evt)
        return evt

    def run(self) -> dict[str, Any]:
        """Execute full autonomous procurement pipeline with session isolation and preference awareness."""
        record_session(
            session_id=self.session_id,
            product=self.product,
            qty=self.qty,
            budget=self.budget,
            status="negotiating",
            is_demo=self.demo_mode,
            negotiation_mode=self.negotiation_mode.value,
            payment_mode=self.payment_mode.value,
            buyer_id=self.buyer_id,
            priority_type=(self.preferences.priority_type.value if self.preferences else None),
        )

        logger.info(
            "[%s] Stage 3: Negotiation engine initialized for product='%s', qty=%d, budget=$%.2f. Mode: %s/%s",
            self.session_id,
            self.product,
            self.qty,
            self.budget,
            self.negotiation_mode.value,
            self.payment_mode.value,
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

        mode_desc = f"{self.negotiation_mode.value} negotiation, {self.payment_mode.value} settlement"
        pref_desc = f" (Priority: {self.preferences.priority_type.value})" if self.preferences else ""
        self._emit(
            agent="System",
            message=f"Starting procurement session for {self.qty} units of '{self.product}' (Budget cap: ${self.budget:.2f}/unit) [{mode_desc}]{pref_desc}.",
            status="negotiating",
            event_type="procurement_start",
        )

        # 2. Buyer Strategy Formulation: Align with buyer preference priorities
        if self.preferences:
            p_type = self.preferences.priority_type
            if p_type == BuyerPreferencePriority.FASTEST_DELIVERY:
                initial_strategy = "EXPEDITED_DELIVERY_AND_TERMS"
                target_discount_unit = round(self.budget * 0.90, 2)
            elif p_type == BuyerPreferencePriority.QUALITY_WARRANTY:
                initial_strategy = "WARRANTY_EXPANSION_AND_FREIGHT_CONCESSION"
                target_discount_unit = round(self.budget * 0.90, 2)
            elif p_type == BuyerPreferencePriority.LOWEST_PRICE:
                initial_strategy = "PRICE_DISCOUNT"
                target_discount_unit = round(self.budget * 0.78, 2)
            else:
                initial_strategy = "PRICE_DISCOUNT"
                target_discount_unit = round(self.budget * 0.85, 2)
        else:
            initial_strategy = "PRICE_DISCOUNT"
            target_discount_unit = round(self.budget * 0.80, 2)

        record_audit_event(
            session_id=self.session_id,
            action="STRATEGY_FORMULATION",
            agent="Buyer Agent",
            reasoning=(
                f"Initial strategy set to '{initial_strategy}' reflecting "
                f"buyer priority '{self.preferences.priority_type.value if self.preferences else 'DEFAULT'}'. "
                f"Target price: ${target_discount_unit:.2f}/unit."
            ),
            details={
                "strategy": initial_strategy,
                "target_price": target_discount_unit,
                "preferences": self.preferences.model_dump() if self.preferences else None,
            },
        )

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
        logger.info("[%s] Stage 4: Round 1 started. Requesting opening terms from 3 sellers.", self.session_id)
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
        logger.info("[%s] Stage 4: Round 1 completed. Received opening offers from all 3 sellers.", self.session_id)

        # 4. Genuinely Autonomous Buyer Evaluation & Concrete Proposals
        buyer_decision = self._buyer_evaluate_and_decide(round1_offers)
        current_strategy = buyer_decision["chosen_strategy"]
        proposals_by_seller = buyer_decision["proposals"]
        logger.info(
            "[%s] Stage 5: Buyer strategy selected: '%s'. Rationale: %s",
            self.session_id,
            current_strategy,
            buyer_decision["strategy_reasoning"],
        )

        record_audit_event(
            session_id=self.session_id,
            action="STRATEGY_ADAPTATION",
            agent="Buyer Agent",
            reasoning=buyer_decision["strategy_reasoning"],
            details={
                "chosen_strategy": current_strategy,
                "proposals": {k: v.model_dump() for k, v in proposals_by_seller.items()},
            },
        )

        self._emit(
            agent="Buyer Agent",
            message=f"Autonomous State Analysis: {buyer_decision['strategy_reasoning']}",
            status="negotiating",
            event_type="strategy_switch",
            strategy=current_strategy,
        )

        # 5. Round 2: Sellers Evaluate Buyer's Concrete Structured Proposals
        logger.info("[%s] Stage 6: Round 2 started. Presenting structured proposals to sellers.", self.session_id)
        final_offers: dict[str, CommercialOffer] = {}
        for key, p in self.sellers_info.items():
            r1 = round1_offers[key]
            prop = proposals_by_seller.get(key)
            offer_r2 = self._seller_round2_decision(key, r1, prop, current_strategy)
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
        logger.info("[%s] Stage 6: Round 2 completed. Received final counteroffers from all 3 sellers.", self.session_id)

        # 6. Deterministic Multi-Factor Commercial Scoring with Constraints
        scored_candidates: list[dict[str, Any]] = []
        for key, offer in final_offers.items():
            p = self.sellers_info[key]
            metrics = get_supplier_metrics(p["name"])
            breakdown = score_offer(
                offer=offer,
                budget_cap=self.budget,
                reliability_score=metrics["reliability_score"],
                preferences=self.preferences,
            )
            scored_candidates.append(
                {
                    "key": key,
                    "name": p["name"],
                    "offer": offer,
                    "score_breakdown": breakdown,
                    "total_score": breakdown.total_score,
                    "is_disqualified": breakdown.is_disqualified,
                    "violations": breakdown.constraint_violations,
                }
            )
        logger.info("[%s] Stage 7: Commercial scoring completed across all 5 dimensions.", self.session_id)

        # Honor hard constraints: prioritize qualified candidates
        qualified_candidates = [c for c in scored_candidates if not c["is_disqualified"]]
        candidates_to_rank = qualified_candidates if qualified_candidates else scored_candidates
        candidates_to_rank.sort(key=lambda x: x["total_score"], reverse=True)

        winner = candidates_to_rank[0]
        winning_name = winner["name"]
        winning_offer: CommercialOffer = winner["offer"]
        winning_breakdown: CommercialScoreBreakdown = winner["score_breakdown"]
        logger.info(
            "[%s] Stage 8: Winning supplier selected: '%s' with commercial score %.1f/100.",
            self.session_id,
            winning_name,
            winning_breakdown.total_score,
        )

        # Record strategy outcome for procurement intelligence (1 per session, based on winning deal & concessions)
        winner_key = winner["key"]
        winner_r1_price = round1_offers[winner_key].unit_price
        winner_final_price = winner["offer"].unit_price
        any_concession = any(
            round1_offers[c["key"]].unit_price > c["offer"].unit_price or
            round1_offers[c["key"]].shipping_cost > c["offer"].shipping_cost or
            round1_offers[c["key"]].delivery_days > c["offer"].delivery_days or
            round1_offers[c["key"]].warranty_months < c["offer"].warranty_months
            for c in scored_candidates
        )
        record_strategy_outcome(
            session_id=self.session_id,
            strategy=current_strategy,
            supplier_name=winning_name,
            initial_price=winner_r1_price,
            final_price=winner_final_price,
            concession_obtained=any_concession,
            won=True,
        )

        # Build 3-Supplier Comparison Summary
        final_offers_summary: list[dict[str, Any]] = []
        for key, offer in final_offers.items():
            p = self.sellers_info[key]
            cand = next((c for c in scored_candidates if c["key"] == key), None)
            r1_unit = round1_offers[key].unit_price
            fin_unit = offer.unit_price
            price_cut = round(r1_unit - fin_unit, 2)
            price_cut_pct = round((price_cut / r1_unit) * 100, 1) if r1_unit > 0 else 0.0
            final_offers_summary.append({
                "vendor": p["name"],
                "archetype": p["archetype"],
                "style": p["style"],
                "specialty": p["specialty"],
                "key": key,
                "unit_price": offer.unit_price,
                "total_price": offer.total_price,
                "quantity": offer.quantity,
                "delivery_days": offer.delivery_days,
                "warranty_months": offer.warranty_months,
                "shipping_cost": offer.shipping_cost,
                "payment_terms": offer.payment_terms,
                "special_concessions": list(offer.special_concessions or []),
                "commercial_score": cand["total_score"] if cand else 0.0,
                "score_breakdown": cand["score_breakdown"].model_dump() if cand else {},
                "is_winner": (key == winner["key"]),
                "is_disqualified": cand["is_disqualified"] if cand else False,
                "violations": cand["violations"] if cand else [],
                "initial_unit_price": r1_unit,
                "price_reduction": price_cut,
                "price_reduction_pct": price_cut_pct,
            })

        # Build Buyer Intelligence Summary
        budget_total = round(self.budget * self.qty, 2)
        final_deal_total = winning_offer.total_price
        budget_headroom = round(budget_total - final_deal_total, 2)
        budget_headroom_unit = round(self.budget - winning_offer.unit_price, 2)
        win_r1_price = round1_offers[winner["key"]].unit_price
        negotiated_savings = round((win_r1_price - winning_offer.unit_price) * self.qty, 2)
        negotiated_savings_unit = round(win_r1_price - winning_offer.unit_price, 2)
        budget_utilization_pct = round((final_deal_total / budget_total) * 100, 1) if budget_total > 0 else 100.0

        trade_offs = []
        for fo in final_offers_summary:
            trade_offs.append(
                f"{fo['vendor']}: ${fo['unit_price']:.2f}/unit ({fo['delivery_days']}d lead, {fo['warranty_months']}mo warranty, freight ${fo['shipping_cost']:.2f})"
            )
        trade_offs_summary = " | ".join(trade_offs)

        win_metrics = get_supplier_metrics(winning_name)
        reliability_notes = (
            f"{winning_name}: Reliability score {win_metrics['reliability_score']}/100 "
            f"({win_metrics['captured_payments']} settled out of {win_metrics['attempted_deals']} negotiations)."
        )

        buyer_intelligence = {
            "recommended_supplier": winning_name,
            "recommended_reason": winning_breakdown.explanation,
            "final_deal_total": final_deal_total,
            "unit_price": winning_offer.unit_price,
            "budget_cap": self.budget,
            "budget_total": budget_total,
            "budget_utilization_pct": budget_utilization_pct,
            "budget_headroom": budget_headroom,
            "budget_headroom_unit": budget_headroom_unit,
            "negotiated_savings": negotiated_savings,
            "negotiated_savings_unit": negotiated_savings_unit,
            "initial_offered_price": win_r1_price,
            "price_reduction_pct": round((negotiated_savings_unit / win_r1_price) * 100, 1) if win_r1_price > 0 else 0.0,
            "trade_offs": trade_offs_summary,
            "reliability_notes": reliability_notes,
            "strategy_pivot_reasoning": buyer_decision["strategy_reasoning"],
        }

        record_audit_event(
            session_id=self.session_id,
            action="VENDOR_SELECTION",
            agent="Buyer Agent",
            reasoning=(
                f"Selected {winning_name} with commercial score {winning_breakdown.total_score}/100. "
                f"Commercial Rationale: {winning_breakdown.explanation}"
            ),
            details={
                "winner": winning_name,
                "score": winning_breakdown.total_score,
                "breakdown": winning_breakdown.model_dump(),
                "disqualified_count": len([c for c in scored_candidates if c["is_disqualified"]]),
            },
        )

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

        # 7. Order Execution & Settlement (Payment Mode Separation)
        if self.payment_mode == PaymentMode.SIMULATED:
            # Deterministic Simulated Payment: Zero PayPal external calls, no fake PayPal URLs
            order_id = f"DEMO-{uuid.uuid4().hex[:12].upper()}" if self.demo_mode else f"SIM-{uuid.uuid4().hex[:12].upper()}"
            record_deal_accepted(
                session_id=self.session_id,
                order_id=order_id,
                supplier_name=winning_name,
                amount=winning_offer.total_price,
                payment_mode="SIMULATED",
            )
            logger.info("[%s] Stage 9: Deal recorded in persistent memory. Order ID: %s", self.session_id, order_id)
            self._emit(
                agent="System",
                message=(
                    f"SIMULATED DEAL APPROVED: Order {order_id} awaiting settlement confirmation. "
                    f"Deal locked at ${winning_offer.unit_price:.2f}/unit (${winning_offer.total_price:.2f} total) with {winning_name}. "
                    "Payment mode: SIMULATED (no external payment provider calls)."
                ),
                status="awaiting_approval",
                event_type="order_created",
                offer=winning_offer.unit_price,
                details={"order_id": order_id, "payment_mode": "SIMULATED"},
            )
            self._emit(
                agent="System",
                message=(
                    f"PROCUREMENT COMPLETED: Deal finalized with {winning_name}. "
                    f"Budget Headroom: ${budget_headroom:.2f} (Utilization: {budget_utilization_pct:.1f}%). "
                    f"Actual Negotiated Price Cut: ${negotiated_savings:.2f} vs opening quote."
                ),
                status="awaiting_approval",
                event_type="procurement_completed",
                offer=winning_offer.unit_price,
                details={
                    "order_id": order_id,
                    "winner": winning_name,
                    "budget_headroom": budget_headroom,
                    "negotiated_savings": negotiated_savings,
                },
            )
            logger.info("[%s] Stage 10: Payment preparation completed (SIMULATED). Ready for instant settlement.", self.session_id)
            record_session(
                session_id=self.session_id,
                product=self.product,
                qty=self.qty,
                budget=self.budget,
                status="awaiting_approval",
                is_demo=self.demo_mode,
                negotiation_mode=self.negotiation_mode.value,
                payment_mode=self.payment_mode.value,
                buyer_id=self.buyer_id,
                priority_type=(self.preferences.priority_type.value if self.preferences else None),
            )
            return {
                "session_id": self.session_id,
                "status": "awaiting_approval",
                "order_id": order_id,
                "approve_url": None,
                "payment_mode": "SIMULATED",
                "negotiation_mode": self.negotiation_mode.value,
                "vendors": public_vendors,
                "deal": {
                    "selected_vendor": winning_name,
                    "commercial_score": winning_breakdown.total_score,
                    "score_breakdown": winning_breakdown.model_dump(),
                    "terms": winning_offer.model_dump(),
                },
                "final_offers": final_offers_summary,
                "winning_vendor": winning_name,
                "winning_offer": winning_offer.model_dump(),
                "buyer_intelligence": buyer_intelligence,
                "events": self.events,
                "token_usage": (
                    self.groq_client.get_usage_metadata(is_demo=self.demo_mode).model_dump()
                    if self.groq_client
                    else {"total_tokens": 0, "total_calls": 0, "is_demo": True}
                ),
            }

        # Live PayPal Sandbox Mode: Real PayPal Sandbox order creation
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
                payment_mode="PAYPAL_SANDBOX",
            )
            logger.info("[%s] Stage 9: Deal recorded in persistent memory. PayPal Order ID: %s", self.session_id, order_id)
            self._emit(
                agent="System",
                message=(
                    f"PayPal Sandbox Order {order_id} created successfully. "
                    "Awaiting human buyer authorization on PayPal Sandbox."
                ),
                status="awaiting_approval",
                event_type="order_created",
                approve_url=approve_url,
                offer=winning_offer.unit_price,
                details={"order_id": order_id, "payment_mode": "PAYPAL_SANDBOX", "approve_url": approve_url},
            )
            self._emit(
                agent="System",
                message=(
                    f"PROCUREMENT COMPLETED: Deal finalized with {winning_name}. "
                    f"Budget Headroom: ${budget_headroom:.2f} (Utilization: {budget_utilization_pct:.1f}%). "
                    f"Actual Negotiated Price Cut: ${negotiated_savings:.2f} vs opening quote."
                ),
                status="awaiting_approval",
                event_type="procurement_completed",
                offer=winning_offer.unit_price,
                details={
                    "order_id": order_id,
                    "winner": winning_name,
                    "budget_headroom": budget_headroom,
                    "negotiated_savings": negotiated_savings,
                    "approve_url": approve_url,
                },
            )
            logger.info("[%s] Stage 10: Payment preparation completed (PAYPAL_SANDBOX). Approval URL ready.", self.session_id)
            record_session(
                session_id=self.session_id,
                product=self.product,
                qty=self.qty,
                budget=self.budget,
                status="awaiting_approval",
                is_demo=self.demo_mode,
                negotiation_mode=self.negotiation_mode.value,
                payment_mode=self.payment_mode.value,
                buyer_id=self.buyer_id,
                priority_type=(self.preferences.priority_type.value if self.preferences else None),
            )
            return {
                "session_id": self.session_id,
                "status": "awaiting_approval",
                "order_id": order_id,
                "approve_url": approve_url,
                "payment_mode": "PAYPAL_SANDBOX",
                "negotiation_mode": self.negotiation_mode.value,
                "vendors": public_vendors,
                "deal": {
                    "selected_vendor": winning_name,
                    "commercial_score": winning_breakdown.total_score,
                    "score_breakdown": winning_breakdown.model_dump(),
                    "terms": winning_offer.model_dump(),
                },
                "final_offers": final_offers_summary,
                "winning_vendor": winning_name,
                "winning_offer": winning_offer.model_dump(),
                "buyer_intelligence": buyer_intelligence,
                "events": self.events,
                "token_usage": (
                    self.groq_client.get_usage_metadata(is_demo=False).model_dump()
                    if self.groq_client
                    else {"total_tokens": 0, "total_calls": 0, "is_demo": True}
                ),
            }
        except Exception as exc:
            logger.error("Failed to create PayPal order: %s", exc)
            self._emit(
                agent="System",
                message=f"PayPal order creation failed: {exc}",
                status="failed",
                event_type="failed",
            )
            record_session(
                session_id=self.session_id,
                product=self.product,
                qty=self.qty,
                budget=self.budget,
                status="failed",
                is_demo=self.demo_mode,
                negotiation_mode=self.negotiation_mode.value,
                payment_mode=self.payment_mode.value,
                buyer_id=self.buyer_id,
                priority_type=(self.preferences.priority_type.value if self.preferences else None),
            )
            return {
                "session_id": self.session_id,
                "status": "failed",
                "order_id": None,
                "approve_url": None,
                "payment_mode": "PAYPAL_SANDBOX",
                "negotiation_mode": self.negotiation_mode.value,
                "vendors": public_vendors,
                "events": self.events,
                "deal": {
                    "selected_vendor": winning_name,
                    "commercial_score": winning_breakdown.total_score,
                    "score_breakdown": winning_breakdown.model_dump(),
                    "terms": winning_offer.model_dump(),
                },
                "final_offers": final_offers_summary,
                "winning_vendor": winning_name,
                "winning_offer": winning_offer.model_dump(),
                "buyer_intelligence": buyer_intelligence,
                "token_usage": (
                    self.groq_client.get_usage_metadata(is_demo=False).model_dump()
                    if self.groq_client
                    else {"total_tokens": 0, "total_calls": 0, "is_demo": True}
                ),
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
        # Evaluates actual numerical positions of the sellers and buyer preferences:
        min_offered_price = min(o.unit_price for o in r1_offers.values())
        discount_percentage = (self.budget - min_offered_price) / self.budget

        if self.preferences and self.preferences.priority_type == BuyerPreferencePriority.FASTEST_DELIVERY:
            chosen_strategy = "EXPEDITED_DELIVERY_AND_TERMS"
            reasoning = (
                "Buyer priority is FASTEST_DELIVERY. Pivoting strategy to 'EXPEDITED_DELIVERY_AND_TERMS' "
                "to secure accelerated dispatch lead times and priority logistics concessions."
            )
            proposals = {
                "seller_a": BuyerProposal(
                    target_unit_price=r1_offers["seller_a"].unit_price,
                    target_delivery_days=7,
                    target_warranty_months=12,
                    target_shipping_cost=r1_offers["seller_a"].shipping_cost,
                    target_payment_terms="Net 30",
                    rationale="Agreeing to unit price if dispatch lead time is reduced to 7 business days.",
                    concession_requested="Accelerated 7-day dispatch",
                ),
                "seller_b": BuyerProposal(
                    target_unit_price=r1_offers["seller_b"].unit_price,
                    target_delivery_days=9,
                    target_warranty_months=18,
                    target_shipping_cost=r1_offers["seller_b"].shipping_cost,
                    target_payment_terms="Net 30",
                    rationale="Holding unit price in exchange for expedited quality-certified delivery.",
                    concession_requested="Priority delivery queue",
                ),
                "seller_c": BuyerProposal(
                    target_unit_price=r1_offers["seller_c"].unit_price,
                    target_delivery_days=4,
                    target_warranty_months=12,
                    target_shipping_cost=0.0,
                    target_payment_terms="Net 60",
                    rationale="Leveraging logistics specialty for ultra-fast 4-day delivery.",
                    concession_requested="Express 4-day delivery and free shipping",
                ),
            }
        elif self.preferences and self.preferences.priority_type == BuyerPreferencePriority.QUALITY_WARRANTY:
            chosen_strategy = "WARRANTY_EXPANSION_AND_FREIGHT_CONCESSION"
            reasoning = (
                "Buyer priority is QUALITY_WARRANTY. Pivoting strategy to 'WARRANTY_EXPANSION_AND_FREIGHT_CONCESSION' "
                "to secure comprehensive warranty coverage and zero freight costs."
            )
            proposals = {
                "seller_a": BuyerProposal(
                    target_unit_price=r1_offers["seller_a"].unit_price,
                    target_delivery_days=10,
                    target_warranty_months=18,
                    target_shipping_cost=0.0,
                    target_payment_terms="Net 30",
                    rationale="Accepting your unit price if freight shipping is absorbed and warranty is extended.",
                    concession_requested="Waived freight shipping and 18-month warranty",
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
                    target_warranty_months=18,
                    target_shipping_cost=0.0,
                    target_payment_terms="Net 60",
                    rationale="Requesting extended warranty coverage, Net 60 payment terms, and free freight.",
                    concession_requested="Extended warranty and Net 60 terms",
                ),
            }
        elif discount_percentage >= 0.12 or (self.preferences and self.preferences.priority_type == BuyerPreferencePriority.LOWEST_PRICE):
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
            # Hard bargainer: firm, target price, basic warranty, industrial freight
            price = round(max(c.min_unit_price + 3.0, self.budget * 0.93), 2)
            shipping = 120.0
            return CommercialOffer(
                unit_price=price,
                total_price=round(price * self.qty + shipping, 2),
                quantity=self.qty,
                delivery_days=10,
                warranty_months=12,
                shipping_cost=shipping,
                payment_terms="Net 30",
                special_concessions=["Standard Industrial Freight"],
            )
        elif seller_key == "seller_b":
            # Value defender: protects price, offers solid initial warranty
            price = round(max(c.min_unit_price + 4.0, self.budget * 0.96), 2)
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
            # Strategic flexible: balanced opening price, fast dispatch
            price = round(max(c.min_unit_price + 2.0, self.budget * 0.90), 2)
            shipping = 60.0
            return CommercialOffer(
                unit_price=price,
                total_price=round(price * self.qty + shipping, 2),
                quantity=self.qty,
                delivery_days=7,
                warranty_months=12,
                shipping_cost=shipping,
                payment_terms="Net 30",
                special_concessions=["Priority Dispatch Queue"],
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

        # Deterministic Policy (Feature 3 & 7): Distinct Psychological Bargaining & Requirement Awareness
        concession_text = buyer_proposal.concession_requested.lower() if buyer_proposal else ""
        rationale_text = buyer_proposal.rationale.lower() if buyer_proposal else ""

        if seller_key == "seller_a":
            # The Hard Bargainer (Apex Industrial Corp):
            # Volume & Commitment Driven!
            # If buyer provides volume commitment (qty >= 200), lowest price priority, or requests volume discount:
            is_volume_deal = (
                self.qty >= 200
                or (self.preferences and self.preferences.priority_type == BuyerPreferencePriority.LOWEST_PRICE)
                or "volume" in concession_text
                or "volume" in rationale_text
                or "repeat" in rationale_text
            )
            if is_volume_deal:
                if self.qty >= 500:
                    discount_ratio = 0.72
                elif self.qty >= 200 or (self.preferences and self.preferences.priority_type == BuyerPreferencePriority.LOWEST_PRICE):
                    discount_ratio = 0.75
                else:
                    discount_ratio = 0.78
                new_price = round(max(c.min_unit_price, self.budget * discount_ratio), 2)
                price_cut = round(r1_offer.unit_price - new_price, 2)
                return CommercialOffer(
                    unit_price=new_price,
                    total_price=round(new_price * self.qty + 90.0, 2),
                    quantity=self.qty,
                    delivery_days=10,
                    warranty_months=12,
                    shipping_cost=90.0,
                    payment_terms="Net 30",
                    special_concessions=[
                        f"Scale Volume Rebate (${price_cut:.2f}/unit reduction)",
                        "Discounted Industrial Freight ($90.00)",
                    ],
                )
            else:
                # Firm refusal: Does NOT concede price without volume commitment!
                return CommercialOffer(
                    unit_price=r1_offer.unit_price,
                    total_price=r1_offer.total_price,
                    quantity=self.qty,
                    delivery_days=10,
                    warranty_months=12,
                    shipping_cost=r1_offer.shipping_cost,
                    payment_terms=r1_offer.payment_terms,
                    special_concessions=["Price Firm — Volume Commitment Required for Discounts"],
                )

        elif seller_key == "seller_b":
            # The Value Defender (Sterling Quality Works):
            # Value & Service Protection. Resists unit price erosion, but grants superior warranty & free shipping.
            is_quality_priority = (
                (self.preferences and self.preferences.priority_type == BuyerPreferencePriority.QUALITY_WARRANTY)
                or (
                    self.preferences
                    and self.preferences.constraints
                    and self.preferences.constraints.min_warranty_months
                    and self.preferences.constraints.min_warranty_months >= 24
                )
            )
            warranty_months = 36 if is_quality_priority else 24
            new_price = round(max(c.min_unit_price, self.budget * 0.90), 2)
            concessions = [
                f"Comprehensive {warranty_months}-Month Engineering Warranty",
                "100% Free Freight Shipping",
                "ISO 9001 Batch QA Certificate",
            ]
            return CommercialOffer(
                unit_price=new_price,
                total_price=round(new_price * self.qty, 2),
                quantity=self.qty,
                delivery_days=12,
                warranty_months=warranty_months,
                shipping_cost=0.0,  # 100% Free Freight
                payment_terms="Net 30",
                special_concessions=concessions,
            )

        else:  # seller_c
            # The Strategic Flexible Negotiator (Vanguard Logistics & Supply):
            # Capacity and Flexibility Driven. Fastest turnaround (4 days), Net 60 terms, free standard logistics.
            new_price = round(max(c.min_unit_price, self.budget * 0.84), 2)
            return CommercialOffer(
                unit_price=new_price,
                total_price=round(new_price * self.qty, 2),
                quantity=self.qty,
                delivery_days=4,
                warranty_months=12,
                shipping_cost=0.0,  # Free shipping
                payment_terms="Net 60",
                special_concessions=[
                    "Expedited 4-Day JIT Delivery",
                    "Extended Net 60 Commercial Terms",
                    "Free Standard Logistics",
                ],
            )
