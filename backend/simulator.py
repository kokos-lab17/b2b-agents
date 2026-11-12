"""What-if procurement scenario simulator (Requirement 9)."""
from typing import Any
from backend.models import (
    BuyerPreferencePriority,
    BuyerPreferences,
    CommercialOffer,
    WhatIfSimulationRequest,
    WhatIfSimulationResult,
)
from backend.personas import SELLER_PERSONAS, generate_private_constraints
from backend.scoring import ScoringWeights, score_offer


def run_what_if_simulation(req: WhatIfSimulationRequest) -> WhatIfSimulationResult:
    """Deterministically simulate procurement outcomes across the 3 seller personas.
    Uses zero Groq calls and creates zero PayPal orders (Requirement 9).
    """
    prefs = BuyerPreferences(
        priority_type=req.priority_type,
        custom_weights=req.weights,
    )
    if req.max_delivery_days or req.min_warranty_months:
        prefs.constraints.max_delivery_days = req.max_delivery_days
        prefs.constraints.min_warranty_months = req.min_warranty_months
        prefs.constraints.max_budget = req.budget

    weights = ScoringWeights.from_preferences(prefs)

    supplier_simulations: list[dict[str, Any]] = []
    best_score = -1.0
    recommended_winner: str | None = None
    winning_total_cost: float = req.budget * req.qty

    for key, persona in SELLER_PERSONAS.items():
        # Generate archetype baseline constraints
        c = generate_private_constraints(key, req.budget, req.qty)

        # Expected concessions based on supplier archetype
        if key == "seller_a":
            # The Hard Bargainer: price sensitive, higher warranty if requested
            est_unit_price = round(max(c.min_unit_price, req.budget * 0.94), 2)
            est_delivery = max(c.min_delivery_days, 10)
            est_warranty = min(c.max_warranty_months, max(12, req.min_warranty_months or 12))
            est_shipping = c.min_shipping_cost
            est_terms = "Net 30"
        elif key == "seller_b":
            # The Value Defender: strong warranty & quality, firm on price
            est_unit_price = round(max(c.min_unit_price, req.budget * 0.98), 2)
            est_delivery = max(c.min_delivery_days, 8)
            est_warranty = min(c.max_warranty_months, max(24, req.min_warranty_months or 24))
            est_shipping = round(c.min_shipping_cost * 0.5, 2)
            est_terms = "Net 30"
        else:
            # The Strategic Flexible Negotiator: fast delivery, shipping concessions
            est_unit_price = round(max(c.min_unit_price, req.budget * 0.96), 2)
            est_delivery = max(c.min_delivery_days, min(5, req.max_delivery_days or 5))
            est_warranty = min(c.max_warranty_months, 12)
            est_shipping = 0.0  # Concedes freight
            est_terms = "Net 60"

        est_total = round(est_unit_price * req.qty + est_shipping, 2)

        offer = CommercialOffer(
            unit_price=est_unit_price,
            total_price=est_total,
            quantity=req.qty,
            delivery_days=est_delivery,
            warranty_months=est_warranty,
            shipping_cost=est_shipping,
            payment_terms=est_terms,
            special_concessions=["Simulated concession package"],
        )

        breakdown = score_offer(
            offer=offer,
            budget_cap=req.budget,
            reliability_score=0.90,
            weights=weights,
            preferences=prefs,
        )

        sim_item = {
            "supplier_name": persona["name"],
            "archetype": persona["archetype"],
            "estimated_unit_price": est_unit_price,
            "estimated_total_cost": est_total,
            "estimated_delivery_days": est_delivery,
            "estimated_warranty_months": est_warranty,
            "estimated_shipping_cost": est_shipping,
            "estimated_payment_terms": est_terms,
            "commercial_score": breakdown.total_score,
            "is_disqualified": breakdown.is_disqualified,
            "violations": breakdown.constraint_violations,
            "score_breakdown": breakdown.model_dump(),
        }
        supplier_simulations.append(sim_item)

    # Pick recommended winner prioritizing qualified candidates
    qualified_sims = [s for s in supplier_simulations if not s["is_disqualified"]]
    if qualified_sims:
        qualified_sims.sort(key=lambda x: x["commercial_score"], reverse=True)
        recommended_winner = qualified_sims[0]["supplier_name"]
        winning_total_cost = qualified_sims[0]["estimated_total_cost"]
    else:
        recommended_winner = "None (All Disqualified)"
        winning_total_cost = 0.0

    return WhatIfSimulationResult(
        product=req.product,
        qty=req.qty,
        budget=req.budget,
        priority_type=req.priority_type.value,
        suppliers=supplier_simulations,
        recommended_winner=recommended_winner,
        estimated_total_cost=winning_total_cost,
    )
