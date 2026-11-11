"""Independent agent personas, distinct negotiation psychology, and private constraints (Feature 1 & 2)."""
import random
from typing import Any
from backend.models import CommercialOffer, PrivateSellerConstraint, SupplierProfile

# Distinct Seller Personas
SELLER_PERSONAS: dict[str, dict[str, Any]] = {
    "seller_a": {
        "name": "Apex Industrial Corp",
        "archetype": "Volume and Commitment Driven",
        "style": "The Hard Bargainer",
        "specialty": "Bulk Manufacturing, Scaled Fabrication & Freight",
        "psychology": (
            "You are Apex Industrial Corp's lead commercial executive — 'The Hard Bargainer'. "
            "You protect margins firmly and rarely accept opening bids. You push back against "
            "demands for cheap pricing unless the buyer demonstrates substantial order volume "
            "or repeat commitment. You make measured counter-offers and never reveal your floor."
        ),
    },
    "seller_b": {
        "name": "Sterling Quality Works",
        "archetype": "Value and Service Protection",
        "style": "The Value Defender",
        "specialty": "High-Precision Engineering, Extended Warranties & QC",
        "psychology": (
            "You are Sterling Quality Works' sales director — 'The Value Defender'. "
            "You pride yourself on superior engineering and ISO certifications. You resist direct "
            "unit price cuts, arguing that cheap parts cost more in the long run. Instead of slashing price, "
            "you offer enhanced warranties (up to 24-36 months), priority QA inspection, and dedicated "
            "engineering support."
        ),
    },
    "seller_c": {
        "name": "Vanguard Logistics & Supply",
        "archetype": "Capacity and Flexibility Driven",
        "style": "The Strategic Flexible Negotiator",
        "specialty": "Just-In-Time Fulfilment, Staged Delivery & Flexible Terms",
        "psychology": (
            "You are Vanguard Logistics & Supply's director — 'The Strategic Flexible Negotiator'. "
            "You are calculating and pragmatic. You actively look for creative win-win trades. "
            "If the buyer cannot move on price, you negotiate on delivery lead times, absorption "
            "of shipping costs, or generous payment terms (Net 60) to optimize warehouse capacity."
        ),
    },
}

BUYER_PERSONA = {
    "role": "Autonomous Commercial Procurement Director",
    "psychology": (
        "You are an autonomous senior B2B procurement director. Your mandate is not simply to "
        "find the cheapest supplier, but to optimize total commercial value (price, warranty, "
        "shipping, lead time, and payment terms). When a supplier firmly rejects price reductions, "
        "you autonomously pivot strategies to extract non-price value such as extended warranties, "
        "waived freight costs, expedited delivery, or extended payment terms. You act decisively."
    ),
}


def generate_private_constraints(
    seller_key: str, budget: float, qty: int, seed: int | None = None
) -> PrivateSellerConstraint:
    """Dynamically generate session-specific private reservation constraints.
    Enforces that floor prices and concession boundaries are never hardcoded or exposed.
    """
    rng = random.Random(seed) if seed is not None else random.Random()

    if seller_key == "seller_a":
        # Hard bargainer: floor between 76% and 86% of budget; target 92-98%
        floor = round(budget * rng.uniform(0.76, 0.86), 2)
        target = round(budget * rng.uniform(0.92, 0.98), 2)
        return PrivateSellerConstraint(
            min_unit_price=floor,
            target_unit_price=max(target, floor + 1.0),
            min_delivery_days=rng.randint(7, 12),
            max_warranty_months=12,
            min_shipping_cost=50.0,
            allowed_payment_terms=["Net 30", "Due on Delivery", "Prepaid"],
        )
    elif seller_key == "seller_b":
        # Value defender: floor between 82% and 90% of budget; target 95-102%
        # Strong warranty capacity (up to 36 months)
        floor = round(budget * rng.uniform(0.82, 0.90), 2)
        target = round(budget * rng.uniform(0.95, 1.02), 2)
        return PrivateSellerConstraint(
            min_unit_price=floor,
            target_unit_price=max(target, floor + 1.0),
            min_delivery_days=rng.randint(10, 16),
            max_warranty_months=36,
            min_shipping_cost=0.0,
            allowed_payment_terms=["Net 30", "Net 60", "Due on Delivery"],
        )
    else:  # seller_c
        # Strategic flexible: floor between 72% and 82% of budget; target 88-94%
        # Flexible delivery & payment terms
        floor = round(budget * rng.uniform(0.72, 0.82), 2)
        target = round(budget * rng.uniform(0.88, 0.94), 2)
        return PrivateSellerConstraint(
            min_unit_price=floor,
            target_unit_price=max(target, floor + 1.0),
            min_delivery_days=rng.randint(4, 8),
            max_warranty_months=18,
            min_shipping_cost=0.0,
            allowed_payment_terms=["Net 60", "Net 30", "Due on Delivery"],
        )


def get_public_supplier_profiles() -> list[SupplierProfile]:
    """Return public supplier profiles without confidential constraints."""
    profiles = []
    for key, data in SELLER_PERSONAS.items():
        profiles.append(
            SupplierProfile(
                name=data["name"],
                archetype=data["archetype"],
                style=data["style"],
                specialty=data["specialty"],
            )
        )
    return profiles
