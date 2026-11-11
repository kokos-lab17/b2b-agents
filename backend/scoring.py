"""Deterministic multi-factor commercial scoring engine (Feature 4)."""
from backend.models import CommercialOffer, CommercialScoreBreakdown


class ScoringWeights:
    PRICE: float = 40.0
    DELIVERY: float = 15.0
    WARRANTY: float = 15.0
    SHIPPING: float = 10.0
    PAYMENT_TERMS: float = 10.0
    RELIABILITY: float = 10.0


def score_offer(
    offer: CommercialOffer,
    budget_cap: float,
    reliability_score: float = 0.90,
    weights: ScoringWeights | None = None,
) -> CommercialScoreBreakdown:
    """Deterministically score a commercial offer across 6 factors.
    Returns 0-100 total score with complete breakdown and explainability.
    """
    w = weights or ScoringWeights()

    # 1. Price Competitiveness (Max 40 points)
    # Higher score for lower unit price relative to buyer's budget cap.
    # If unit_price exceeds budget_cap, price score drops sharply.
    if offer.unit_price <= budget_cap:
        # Savings margin from 0% savings (10 pts) up to 40% savings (40 pts)
        savings_pct = (budget_cap - offer.unit_price) / budget_cap
        price_score = min(w.PRICE, max(10.0, 20.0 + (savings_pct * 50.0)))
    else:
        # Penalize over-budget offers
        overage_pct = (offer.unit_price - budget_cap) / budget_cap
        price_score = max(0.0, 10.0 - (overage_pct * 40.0))
    price_score = round(price_score, 2)

    # 2. Delivery Time (Max 15 points)
    # Faster is better: 3 days or less = full 15 pts; 30+ days = 2 pts.
    if offer.delivery_days <= 3:
        delivery_score = w.DELIVERY
    elif offer.delivery_days >= 30:
        delivery_score = 2.0
    else:
        delivery_score = w.DELIVERY - ((offer.delivery_days - 3) / (30 - 3)) * (w.DELIVERY - 2.0)
    delivery_score = round(max(0.0, min(w.DELIVERY, delivery_score)), 2)

    # 3. Warranty Duration (Max 15 points)
    # 36 months = 15 pts; 24 months = 12 pts; 12 months = 8 pts; 0 months = 0 pts.
    warranty_score = round(min(w.WARRANTY, (offer.warranty_months / 36.0) * w.WARRANTY), 2)

    # 4. Shipping Cost (Max 10 points)
    # $0 (Free shipping) = 10 pts; higher costs scaled down.
    if offer.shipping_cost <= 0:
        shipping_score = w.SHIPPING
    else:
        # Scaled against total order value
        total_val = offer.quantity * offer.unit_price
        shipping_ratio = offer.shipping_cost / max(total_val, 1.0)
        shipping_score = max(0.0, w.SHIPPING * (1.0 - min(1.0, shipping_ratio * 10.0)))
    shipping_score = round(min(w.SHIPPING, shipping_score), 2)

    # 5. Payment Terms (Max 10 points)
    # Net 60 = 10 pts, Net 30 = 8 pts, Due on Delivery = 5 pts, Prepaid = 2 pts.
    terms_lower = offer.payment_terms.lower()
    if "net 60" in terms_lower or "net 90" in terms_lower:
        terms_score = 10.0
    elif "net 30" in terms_lower:
        terms_score = 8.0
    elif "delivery" in terms_lower or "cod" in terms_lower:
        terms_score = 5.0
    elif "prepaid" in terms_lower or "advance" in terms_lower:
        terms_score = 2.0
    else:
        terms_score = 6.0
    terms_score = round(min(w.PAYMENT_TERMS, terms_score), 2)

    # 6. Supplier Reliability (Max 10 points)
    # Historical track record from memory (0.0 - 1.0)
    rel = max(0.0, min(1.0, reliability_score))
    reliability_score_pts = round(rel * w.RELIABILITY, 2)

    total_score = round(
        price_score
        + delivery_score
        + warranty_score
        + shipping_score
        + terms_score
        + reliability_score_pts,
        2,
    )

    # Explainable commercial rationale
    explanation_parts = [
        f"Unit price ${offer.unit_price:.2f} (budget ${budget_cap:.2f}) contributes {price_score}/{w.PRICE:.0f} pts.",
        f"Delivery lead time {offer.delivery_days} days contributes {delivery_score}/{w.DELIVERY:.0f} pts.",
        f"Warranty of {offer.warranty_months} months contributes {warranty_score}/{w.WARRANTY:.0f} pts.",
        f"Shipping cost of ${offer.shipping_cost:.2f} contributes {shipping_score}/{w.SHIPPING:.0f} pts.",
        f"Payment terms '{offer.payment_terms}' contribute {terms_score}/{w.PAYMENT_TERMS:.0f} pts.",
        f"Supplier historical reliability ({rel * 100:.0f}%) contributes {reliability_score_pts}/{w.RELIABILITY:.0f} pts.",
    ]
    explanation = " | ".join(explanation_parts)

    return CommercialScoreBreakdown(
        total_score=total_score,
        price_score=price_score,
        delivery_score=delivery_score,
        warranty_score=warranty_score,
        shipping_score=shipping_score,
        terms_score=terms_score,
        reliability_score=reliability_score_pts,
        explanation=explanation,
    )
