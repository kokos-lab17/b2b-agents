"""Persistent buyer profile management and consent-based behavioural learning (Requirement 4)."""
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from backend.memory import _get_connection, _db_lock
from backend.models import (
    BuyerConstraints,
    BuyerPreferencePriority,
    BuyerPreferences,
    LearnedRecommendation,
)

logger = logging.getLogger(__name__)


def get_buyer_profile(buyer_id: str = "default_buyer", db_path: str | None = None) -> dict[str, Any]:
    """Retrieve or create persistent buyer profile."""
    with _db_lock:
        conn = _get_connection(db_path)
        try:
            with conn:
                cur = conn.execute("SELECT * FROM buyer_profiles WHERE buyer_id = ?;", (buyer_id,))
                row = cur.fetchone()
                if not row:
                    now = datetime.now(timezone.utc).isoformat()
                    conn.execute("""
                        INSERT INTO buyer_profiles (buyer_id, name, created_at, updated_at)
                        VALUES (?, ?, ?, ?);
                    """, (buyer_id, "Procurement Officer", now, now))
                    return {"buyer_id": buyer_id, "name": "Procurement Officer", "created_at": now, "updated_at": now}
                return dict(row)
        finally:
            conn.close()


def get_saved_buyer_preferences(buyer_id: str = "default_buyer", db_path: str | None = None) -> BuyerPreferences:
    """Retrieve explicit saved buyer preferences from persistent database (Requirement 4.2)."""
    with _db_lock:
        conn = _get_connection(db_path)
        try:
            with conn:
                cur = conn.execute("SELECT * FROM buyer_preferences WHERE buyer_id = ?;", (buyer_id,))
                row = cur.fetchone()
                if not row:
                    return BuyerPreferences(priority_type=BuyerPreferencePriority.BALANCED_VALUE, source="system_default")

                custom_w = json.loads(row["custom_weights_json"]) if row["custom_weights_json"] else None
                constraints = BuyerConstraints(
                    max_budget=row["max_budget"],
                    max_delivery_days=row["max_delivery_days"],
                    min_warranty_months=row["min_warranty_months"],
                    max_shipping_cost=row["max_shipping_cost"],
                    allowed_payment_terms=json.loads(row["preferred_payment_terms"]) if row["preferred_payment_terms"] and row["preferred_payment_terms"].startswith("[") else None,
                )
                try:
                    p_type = BuyerPreferencePriority(row["priority_type"])
                except Exception:
                    p_type = BuyerPreferencePriority.BALANCED_VALUE

                return BuyerPreferences(
                    priority_type=p_type,
                    custom_weights=custom_w,
                    preferred_payment_terms=row["preferred_payment_terms"] if row["preferred_payment_terms"] and not row["preferred_payment_terms"].startswith("[") else None,
                    constraints=constraints,
                    source=row["source"] or "explicit",
                )
        finally:
            conn.close()


def save_buyer_preferences(
    buyer_id: str,
    preferences: BuyerPreferences,
    source: str = "explicit",
    db_path: str | None = None,
) -> BuyerPreferences:
    """Explicitly save buyer preferences (Requirement 4.1 & 4.3)."""
    with _db_lock:
        conn = _get_connection(db_path)
        try:
            with conn:
                now = datetime.now(timezone.utc).isoformat()
                custom_w_json = json.dumps(preferences.custom_weights) if preferences.custom_weights else None
                c = preferences.constraints or BuyerConstraints()
                conn.execute("""
                    INSERT INTO buyer_preferences (
                        buyer_id, priority_type, custom_weights_json, max_budget,
                        max_delivery_days, min_warranty_months, max_shipping_cost,
                        preferred_payment_terms, source, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(buyer_id) DO UPDATE SET
                        priority_type=excluded.priority_type,
                        custom_weights_json=excluded.custom_weights_json,
                        max_budget=excluded.max_budget,
                        max_delivery_days=excluded.max_delivery_days,
                        min_warranty_months=excluded.min_warranty_months,
                        max_shipping_cost=excluded.max_shipping_cost,
                        preferred_payment_terms=excluded.preferred_payment_terms,
                        source=excluded.source,
                        updated_at=excluded.updated_at;
                """, (
                    buyer_id,
                    preferences.priority_type.value,
                    custom_w_json,
                    c.max_budget,
                    c.max_delivery_days,
                    c.min_warranty_months,
                    c.max_shipping_cost,
                    preferences.preferred_payment_terms or (json.dumps(c.allowed_payment_terms) if c.allowed_payment_terms else None),
                    source,
                    now
                ))
                # Ensure buyer profile updated timestamp
                conn.execute("UPDATE buyer_profiles SET updated_at = ? WHERE buyer_id = ?;", (now, buyer_id))
        finally:
            conn.close()
    return preferences


def resolve_effective_preferences(
    session_preferences: BuyerPreferences | None = None,
    buyer_id: str = "default_buyer",
    db_path: str | None = None,
) -> BuyerPreferences:
    """Implement deterministic preference precedence hierarchy (Requirement 4.3):
    1. Current-session buyer instructions.
    2. Explicit saved buyer preferences.
    3. Buyer-approved learned preferences.
    4. System defaults.
    """
    if session_preferences and session_preferences.source == "explicit":
        # Current-session instructions take top precedence
        return session_preferences

    # Check saved explicit preferences or approved learning
    saved = get_saved_buyer_preferences(buyer_id, db_path)
    if saved and saved.source in ("explicit", "approved_learning"):
        if session_preferences:
            # Merge: session constraints override saved ones if explicitly provided
            merged_constraints = BuyerConstraints(
                max_budget=session_preferences.constraints.max_budget or saved.constraints.max_budget,
                max_delivery_days=session_preferences.constraints.max_delivery_days or saved.constraints.max_delivery_days,
                min_warranty_months=session_preferences.constraints.min_warranty_months or saved.constraints.min_warranty_months,
                max_shipping_cost=session_preferences.constraints.max_shipping_cost or saved.constraints.max_shipping_cost,
                allowed_payment_terms=session_preferences.constraints.allowed_payment_terms or saved.constraints.allowed_payment_terms,
            )
            return BuyerPreferences(
                priority_type=session_preferences.priority_type or saved.priority_type,
                custom_weights=session_preferences.custom_weights or saved.custom_weights,
                preferred_payment_terms=session_preferences.preferred_payment_terms or saved.preferred_payment_terms,
                constraints=merged_constraints,
                source="session_merged",
            )
        return saved

    return session_preferences or BuyerPreferences(priority_type=BuyerPreferencePriority.BALANCED_VALUE, source="system_default")


def analyze_and_generate_recommendations(buyer_id: str = "default_buyer", db_path: str | None = None) -> list[LearnedRecommendation]:
    """Deterministically analyze historical procurement decisions to identify behavioral patterns (Requirement 4.4).
    Calculates statistics in Python with ZERO Groq token calls.
    """
    with _db_lock:
        conn = _get_connection(db_path)
        try:
            with conn:
                # Query last 10 completed sessions for this buyer
                cur = conn.execute("""
                    SELECT s.id, s.selected_supplier, s.total_cost, s.qty, s.budget,
                           nr.final_unit_price, nr.rounds
                    FROM procurement_sessions s
                    LEFT JOIN negotiation_records nr ON s.id = nr.session_id AND s.selected_supplier = nr.supplier_name
                    WHERE s.buyer_id = ? AND s.status = 'completed' AND s.selected_supplier IS NOT NULL
                    ORDER BY s.created_at DESC
                    LIMIT 10;
                """, (buyer_id,))
                rows = cur.fetchall()
                if len(rows) < 3:
                    # Need at least 3 completed observations for reliable pattern detection
                    return []

                # Count winning supplier styles
                fast_delivery_wins = sum(1 for r in rows if "Vanguard" in (r["selected_supplier"] or ""))
                warranty_quality_wins = sum(1 for r in rows if "Sterling" in (r["selected_supplier"] or ""))
                hard_price_wins = sum(1 for r in rows if "Apex" in (r["selected_supplier"] or ""))
                total_sample = len(rows)

                recommendations: list[LearnedRecommendation] = []
                now = datetime.now(timezone.utc).isoformat()

                # Check if delivery preference pattern emerges (>= 50% of wins)
                if fast_delivery_wins >= 2 and (fast_delivery_wins / total_sample) >= 0.40:
                    rec_id = f"rec-deliv-{buyer_id}"
                    # Check if already exists
                    cur_exist = conn.execute("SELECT status FROM buyer_recommendations WHERE id = ?;", (rec_id,))
                    existing = cur_exist.fetchone()
                    if not existing or existing["status"] == "pending":
                        rec = LearnedRecommendation(
                            id=rec_id,
                            buyer_id=buyer_id,
                            recommendation_type="priority_delivery",
                            title="Fast Delivery Preference Detected",
                            description="Your recent procurement decisions frequently prioritize expedited delivery and logistics flexibility. Would you like to set Fastest Delivery as your default priority?",
                            evidence=f"In {fast_delivery_wins} of your last {total_sample} completed procurements ({int(fast_delivery_wins/total_sample*100)}%), you selected Vanguard Logistics for expedited delivery.",
                            suggested_priority=BuyerPreferencePriority.FASTEST_DELIVERY.value,
                            status="pending" if not existing else existing["status"],
                            created_at=now,
                            updated_at=now,
                        )
                        conn.execute("""
                            INSERT INTO buyer_recommendations (id, buyer_id, recommendation_type, title, description, evidence, suggested_priority, status, created_at, updated_at)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            ON CONFLICT(id) DO UPDATE SET
                                evidence=excluded.evidence,
                                updated_at=excluded.updated_at;
                        """, (rec.id, rec.buyer_id, rec.recommendation_type, rec.title, rec.description, rec.evidence, rec.suggested_priority, rec.status, now, now))
                        recommendations.append(rec)

                # Check if warranty / quality pattern emerges
                if warranty_quality_wins >= 2 and (warranty_quality_wins / total_sample) >= 0.40:
                    rec_id = f"rec-warr-{buyer_id}"
                    cur_exist = conn.execute("SELECT status FROM buyer_recommendations WHERE id = ?;", (rec_id,))
                    existing = cur_exist.fetchone()
                    if not existing or existing["status"] == "pending":
                        rec = LearnedRecommendation(
                            id=rec_id,
                            buyer_id=buyer_id,
                            recommendation_type="priority_warranty",
                            title="Quality & Warranty Preference Detected",
                            description="Your recent decisions demonstrate a strong preference for extended warranty coverage and quality assurance. Would you like to prioritize Quality & Warranty in future negotiations?",
                            evidence=f"In {warranty_quality_wins} of your last {total_sample} completed procurements ({int(warranty_quality_wins/total_sample*100)}%), you selected Sterling Quality Works for comprehensive warranty protection.",
                            suggested_priority=BuyerPreferencePriority.QUALITY_WARRANTY.value,
                            status="pending" if not existing else existing["status"],
                            created_at=now,
                            updated_at=now,
                        )
                        conn.execute("""
                            INSERT INTO buyer_recommendations (id, buyer_id, recommendation_type, title, description, evidence, suggested_priority, status, created_at, updated_at)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            ON CONFLICT(id) DO UPDATE SET
                                evidence=excluded.evidence,
                                updated_at=excluded.updated_at;
                        """, (rec.id, rec.buyer_id, rec.recommendation_type, rec.title, rec.description, rec.evidence, rec.suggested_priority, rec.status, now, now))
                        recommendations.append(rec)

                return recommendations
        finally:
            conn.close()


def get_pending_recommendations(buyer_id: str = "default_buyer", db_path: str | None = None) -> list[dict[str, Any]]:
    """Fetch unapproved learned recommendations awaiting buyer consent (Requirement 4.4)."""
    # First run analysis to ensure latest stats
    analyze_and_generate_recommendations(buyer_id, db_path)
    with _db_lock:
        conn = _get_connection(db_path)
        try:
            with conn:
                cur = conn.execute("""
                    SELECT id, buyer_id, recommendation_type, title, description, evidence, suggested_priority, status, created_at
                    FROM buyer_recommendations
                    WHERE buyer_id = ? AND status = 'pending';
                """, (buyer_id,))
                return [dict(r) for r in cur.fetchall()]
        finally:
            conn.close()


def accept_recommendation(recommendation_id: str, buyer_id: str = "default_buyer", db_path: str | None = None) -> BuyerPreferences:
    """Buyer explicitly accepts an AI recommendation, saving it as an approved learned preference (Requirement 4.4)."""
    with _db_lock:
        conn = _get_connection(db_path)
        try:
            with conn:
                now = datetime.now(timezone.utc).isoformat()
                cur = conn.execute("""
                    SELECT suggested_priority FROM buyer_recommendations
                    WHERE id = ? AND buyer_id = ?;
                """, (recommendation_id, buyer_id))
                row = cur.fetchone()
                if not row:
                    raise ValueError(f"Recommendation '{recommendation_id}' not found for buyer '{buyer_id}'")

                suggested = row["suggested_priority"]
                conn.execute("""
                    UPDATE buyer_recommendations
                    SET status = 'accepted', updated_at = ?
                    WHERE id = ?;
                """, (now, recommendation_id))
        finally:
            conn.close()

    # Save to buyer preferences with source='approved_learning'
    try:
        priority_enum = BuyerPreferencePriority(suggested)
    except Exception:
        priority_enum = BuyerPreferencePriority.BALANCED_VALUE

    current = get_saved_buyer_preferences(buyer_id, db_path)
    updated = BuyerPreferences(
        priority_type=priority_enum,
        custom_weights=current.custom_weights,
        preferred_payment_terms=current.preferred_payment_terms,
        constraints=current.constraints,
        source="approved_learning",
    )
    return save_buyer_preferences(buyer_id, updated, source="approved_learning", db_path=db_path)


def ignore_recommendation(recommendation_id: str, buyer_id: str = "default_buyer", db_path: str | None = None) -> None:
    """Buyer explicitly ignores or declines an AI recommendation (Requirement 4.4)."""
    with _db_lock:
        conn = _get_connection(db_path)
        try:
            with conn:
                now = datetime.now(timezone.utc).isoformat()
                conn.execute("""
                    UPDATE buyer_recommendations
                    SET status = 'ignored', updated_at = ?
                    WHERE id = ? AND buyer_id = ?;
                """, (now, recommendation_id, buyer_id))
        finally:
            conn.close()
