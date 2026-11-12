"""Deterministic multi-factor commercial scoring engine (Feature 4 & Requirement 4 & 5)."""
from backend.models import (
    BuyerPreferencePriority,
    BuyerPreferences,
    CommercialOffer,
    CommercialScoreBreakdown,
)


class ScoringWeights:
    def __init__(
        self,
        price: float = 40.0,
        delivery: float = 15.0,
        warranty: float = 15.0,
        shipping: float = 10.0,
        payment_terms: float = 10.0,
        reliability: float = 10.0,
    ):
        self.PRICE = price
        self.DELIVERY = delivery
        self.WARRANTY = warranty
        self.SHIPPING = shipping
        self.PAYMENT_TERMS = payment_terms
        self.RELIABILITY = reliability

    @classmethod
    def from_preferences(cls, preferences: BuyerPreferences | None) -> "ScoringWeights":
        """Generate deterministic evaluation weights from buyer priorities (Requirement 4.1)."""
        if not preferences:
            return cls()

        priority = preferences.priority_type
        if priority == BuyerPreferencePriority.LOWEST_PRICE:
            return cls(price=50.0, delivery=10.0, warranty=10.0, shipping=10.0, payment_terms=10.0, reliability=10.0)
        elif priority == BuyerPreferencePriority.FASTEST_DELIVERY:
            return cls(price=30.0, delivery=35.0, warranty=15.0, shipping=10.0, payment_terms=5.0, reliability=5.0)
        elif priority == BuyerPreferencePriority.QUALITY_WARRANTY:
            return cls(price=25.0, delivery=10.0, warranty=35.0, shipping=5.0, payment_terms=10.0, reliability=15.0)
        elif priority == BuyerPreferencePriority.CUSTOM and preferences.custom_weights:
            cw = preferences.custom_weights
            raw_p = max(0.0, float(cw.get("price", 40.0)))
            raw_d = max(0.0, float(cw.get("delivery", 15.0)))
            raw_w = max(0.0, float(cw.get("warranty", 15.0)))
            raw_s = max(0.0, float(cw.get("shipping", 10.0)))
            raw_t = max(0.0, float(cw.get("terms", 10.0)))
            raw_r = max(0.0, float(cw.get("reliability", 10.0)))
            total = raw_p + raw_d + raw_w + raw_s + raw_t + raw_r
            if total > 0:
                scale = 100.0 / total
                return cls(
                    price=round(raw_p * scale, 1),
                    delivery=round(raw_d * scale, 1),
                    warranty=round(raw_w * scale, 1),
                    shipping=round(raw_s * scale, 1),
                    payment_terms=round(raw_t * scale, 1),
                    reliability=round(raw_r * scale, 1),
                )
            return cls()
        return cls()


def score_offer(
    offer: CommercialOffer,
    budget_cap: float,
    reliability_score: float | None = None,
    weights: ScoringWeights | None = None,
    preferences: BuyerPreferences | None = None,
) -> CommercialScoreBreakdown:
    """Deterministically score a commercial offer across 6 factors.
    Strictly checks mandatory constraints before allowing an offer to be eligible (Requirement 4 & 5).
    """
    if weights is not None:
        w = weights
    elif preferences is not None:
        w = ScoringWeights.from_preferences(preferences)
    else:
        w = ScoringWeights()

    # 1. Price Competitiveness (dynamically proportional to w.PRICE weight)
    effective_budget = budget_cap
    if preferences and preferences.constraints and preferences.constraints.max_budget:
        effective_budget = min(budget_cap, preferences.constraints.max_budget)

    if offer.unit_price <= effective_budget:
        savings_pct = (effective_budget - offer.unit_price) / effective_budget
        price_score = min(w.PRICE, (w.PRICE * 0.60) + (savings_pct * w.PRICE * 1.60))
    else:
        overage_pct = (offer.unit_price - effective_budget) / effective_budget
        price_score = max(0.0, (w.PRICE * 0.35) - (overage_pct * w.PRICE * 2.0))
    price_score = round(price_score, 2)

    # 2. Delivery Time
    if offer.delivery_days <= 3:
        delivery_score = w.DELIVERY
    elif offer.delivery_days >= 30:
        delivery_score = max(0.0, w.DELIVERY * 0.15)
    else:
        delivery_score = w.DELIVERY - ((offer.delivery_days - 3) / (30 - 3)) * (w.DELIVERY * 0.85)
    delivery_score = round(max(0.0, min(w.DELIVERY, delivery_score)), 2)

    # 3. Warranty Duration
    warranty_score = round(min(w.WARRANTY, (offer.warranty_months / 36.0) * w.WARRANTY), 2)

    # 4. Shipping Cost
    if offer.shipping_cost <= 0:
        shipping_score = w.SHIPPING
    else:
        total_val = offer.quantity * offer.unit_price
        shipping_ratio = offer.shipping_cost / max(total_val, 1.0)
        shipping_score = max(0.0, w.SHIPPING * (1.0 - min(1.0, shipping_ratio * 10.0)))
    shipping_score = round(min(w.SHIPPING, shipping_score), 2)

    # 5. Payment Terms
    terms_lower = offer.payment_terms.lower()
    if "net 60" in terms_lower or "net 90" in terms_lower:
        terms_score = w.PAYMENT_TERMS
    elif "net 30" in terms_lower:
        terms_score = round(w.PAYMENT_TERMS * 0.8, 2)
    elif "delivery" in terms_lower or "cod" in terms_lower:
        terms_score = round(w.PAYMENT_TERMS * 0.5, 2)
    elif "prepaid" in terms_lower or "advance" in terms_lower:
        terms_score = round(w.PAYMENT_TERMS * 0.2, 2)
    else:
        terms_score = round(w.PAYMENT_TERMS * 0.6, 2)

    # If preferred terms match buyer preference, give full terms score
    if preferences and preferences.preferred_payment_terms:
        if preferences.preferred_payment_terms.lower() in terms_lower:
            terms_score = w.PAYMENT_TERMS
    terms_score = round(min(w.PAYMENT_TERMS, terms_score), 2)

    # 6. Supplier Reliability (historical reliability if known, or neutral 0.85 prior)
    effective_rel = 0.85 if reliability_score is None else reliability_score
    rel = max(0.0, min(1.0, effective_rel))
    reliability_score_pts = round(rel * w.RELIABILITY, 2)

    calculated_total = round(
        price_score
        + delivery_score
        + warranty_score
        + shipping_score
        + terms_score
        + reliability_score_pts,
        2,
    )

    # Deterministic Mandatory Constraints Verification (Requirement 4.1 & 5)
    violations: list[str] = []
    if preferences and preferences.constraints:
        c = preferences.constraints
        if c.max_budget is not None and offer.unit_price > c.max_budget:
            violations.append(f"Unit price ${offer.unit_price:.2f} exceeds mandatory budget limit of ${c.max_budget:.2f}")
        if c.max_delivery_days is not None and offer.delivery_days > c.max_delivery_days:
            violations.append(f"Delivery {offer.delivery_days} days exceeds mandatory max of {c.max_delivery_days} days")
        if c.min_warranty_months is not None and offer.warranty_months < c.min_warranty_months:
            violations.append(f"Warranty {offer.warranty_months} mos is below mandatory min of {c.min_warranty_months} mos")
        if c.max_shipping_cost is not None and offer.shipping_cost > c.max_shipping_cost:
            violations.append(f"Shipping freight ${offer.shipping_cost:.2f} exceeds mandatory cap of ${c.max_shipping_cost:.2f}")
        if c.allowed_payment_terms and offer.payment_terms not in c.allowed_payment_terms:
            violations.append(f"Payment terms '{offer.payment_terms}' not in allowed list {c.allowed_payment_terms}")

    is_disqualified = len(violations) > 0
    final_total_score = 0.0 if is_disqualified else calculated_total

    # Explainable commercial rationale
    explanation_parts = [
        f"Unit price ${offer.unit_price:.2f} (budget ${budget_cap:.2f}) contributes {price_score}/{w.PRICE:.0f} pts.",
        f"Delivery lead time {offer.delivery_days} days contributes {delivery_score}/{w.DELIVERY:.0f} pts.",
        f"Warranty of {offer.warranty_months} months contributes {warranty_score}/{w.WARRANTY:.0f} pts.",
        f"Shipping cost of ${offer.shipping_cost:.2f} contributes {shipping_score}/{w.SHIPPING:.0f} pts.",
        f"Payment terms '{offer.payment_terms}' contribute {terms_score}/{w.PAYMENT_TERMS:.0f} pts.",
        f"Supplier {'historical reliability' if reliability_score is not None else 'baseline prior'} ({rel * 100:.0f}%) contributes {reliability_score_pts}/{w.RELIABILITY:.0f} pts.",
    ]
    explanation = " | ".join(explanation_parts)
    if is_disqualified:
        explanation = f"DISQUALIFIED (Mandatory Constraint Violated: {'; '.join(violations)}). Normal factor score was {calculated_total}/100. " + explanation

    return CommercialScoreBreakdown(
        total_score=final_total_score,
        price_score=price_score,
        delivery_score=delivery_score,
        warranty_score=warranty_score,
        shipping_score=shipping_score,
        terms_score=terms_score,
        reliability_score=reliability_score_pts,
        explanation=explanation,
        is_disqualified=is_disqualified,
        constraint_violations=violations,
    )
