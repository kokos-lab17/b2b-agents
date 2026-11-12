"""Quote and contract comparison engine (Requirement 14)."""
import json
import uuid
from datetime import datetime, timezone
from typing import Any
from backend.memory import _get_connection, _db_lock
from backend.models import (
    CommercialOffer,
    QuoteComparisonItem,
    QuoteComparisonRequest,
)
from backend.scoring import ScoringWeights, score_offer


def compare_quotes(req: QuoteComparisonRequest) -> dict[str, Any]:
    """Deterministically compare structured vendor quotations against buyer constraints and priorities."""
    if not req.quotes:
        return {
            "comparison": [],
            "best_quote": None,
            "disqualified_count": 0,
            "summary": "No quotes provided for comparison.",
        }

    weights = ScoringWeights.from_preferences(req.preferences)
    comparison_results: list[dict[str, Any]] = []
    best_score = -1.0
    best_quote_supplier: str | None = None
    disqualified_count = 0

    # Find minimum price and lead time across quotes for delta comparison
    min_price = min(q.unit_price for q in req.quotes)
    min_delivery = min(q.delivery_days for q in req.quotes)
    max_warranty = max(q.warranty_months for q in req.quotes)

    for q in req.quotes:
        total = round(q.unit_price * q.quantity + q.shipping_cost, 2)
        offer = CommercialOffer(
            unit_price=q.unit_price,
            total_price=total,
            quantity=q.quantity,
            delivery_days=q.delivery_days,
            warranty_months=q.warranty_months,
            shipping_cost=q.shipping_cost,
            payment_terms=q.payment_terms,
            special_concessions=q.special_concessions,
        )

        breakdown = score_offer(
            offer=offer,
            budget_cap=req.budget_cap,
            reliability_score=0.90,
            weights=weights,
            preferences=req.preferences,
        )

        if breakdown.is_disqualified:
            disqualified_count += 1

        delta_price_pct = round(((q.unit_price - min_price) / min_price) * 100, 1) if min_price > 0 else 0.0

        item_result = {
            "supplier_name": q.supplier_name,
            "unit_price": q.unit_price,
            "total_price": total,
            "quantity": q.quantity,
            "delivery_days": q.delivery_days,
            "warranty_months": q.warranty_months,
            "shipping_cost": q.shipping_cost,
            "payment_terms": q.payment_terms,
            "special_concessions": q.special_concessions,
            "commercial_score": breakdown.total_score,
            "is_disqualified": breakdown.is_disqualified,
            "violations": breakdown.constraint_violations,
            "score_breakdown": breakdown.model_dump(),
            "delta_from_cheapest_pct": delta_price_pct,
            "is_fastest": q.delivery_days == min_delivery,
            "is_highest_warranty": q.warranty_months == max_warranty,
            "notes": q.notes,
        }
        comparison_results.append(item_result)

        if not breakdown.is_disqualified and breakdown.total_score > best_score:
            best_score = breakdown.total_score
            best_quote_supplier = q.supplier_name

    # Sort results by score descending
    comparison_results.sort(key=lambda x: x["commercial_score"], reverse=True)

    return {
        "comparison": comparison_results,
        "best_quote": best_quote_supplier,
        "best_quote_supplier": best_quote_supplier,
        "best_score": best_score if best_score >= 0 else None,
        "total_quotes": len(req.quotes),
        "disqualified_count": disqualified_count,
        "summary": (
            f"Recommended supplier based on buyer preferences is {best_quote_supplier} with commercial score {best_score:.1f}/100."
            if best_quote_supplier
            else "All evaluated quotes were disqualified due to mandatory constraint violations."
        ),
    }


def save_quote(quote: QuoteComparisonItem, db_path: str | None = None) -> str:
    """Save a quote in persistent storage."""
    q_id = quote.id or f"quote-{uuid.uuid4().hex[:10]}"
    with _db_lock:
        conn = _get_connection(db_path)
        try:
            with conn:
                now = datetime.now(timezone.utc).isoformat()
                concessions_json = json.dumps(quote.special_concessions) if quote.special_concessions else "[]"
                conn.execute("""
                    INSERT INTO saved_quotes (
                        id, title, supplier_name, unit_price, quantity, delivery_days,
                        warranty_months, shipping_cost, payment_terms, special_concessions_json,
                        notes, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """, (
                    q_id, f"Quote from {quote.supplier_name}", quote.supplier_name,
                    quote.unit_price, quote.quantity, quote.delivery_days, quote.warranty_months,
                    quote.shipping_cost, quote.payment_terms, concessions_json, quote.notes, now
                ))
        finally:
            conn.close()
    return q_id


def get_saved_quotes(limit: int = 20, db_path: str | None = None) -> list[dict[str, Any]]:
    """Retrieve saved quotes from persistent database."""
    with _db_lock:
        conn = _get_connection(db_path)
        try:
            with conn:
                cur = conn.execute("""
                    SELECT * FROM saved_quotes ORDER BY created_at DESC LIMIT ?;
                """, (limit,))
                quotes = []
                for row in cur.fetchall():
                    item = dict(row)
                    if item.get("special_concessions_json"):
                        try:
                            item["special_concessions"] = json.loads(item["special_concessions_json"])
                        except Exception:
                            item["special_concessions"] = []
                    quotes.append(item)
                return quotes
        finally:
            conn.close()
