"""Unit tests for Buyer Preferences, Mandatory Constraints & Learning (Requirement 4)."""
import os
import tempfile
import unittest

from backend.buyer_preferences import (
    accept_recommendation,
    analyze_and_generate_recommendations,
    get_buyer_profile,
    get_pending_recommendations,
    get_saved_buyer_preferences,
    ignore_recommendation,
    resolve_effective_preferences,
    save_buyer_preferences,
)
from backend.memory import init_db, record_deal_accepted, record_negotiation_attempt, record_payment_captured, record_session
from backend.models import (
    BuyerConstraints,
    BuyerPreferencePriority,
    BuyerPreferences,
    CommercialOffer,
)
from backend.scoring import score_offer


class TestBuyerPreferences(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = self.tmp.name
        self.tmp.close()
        init_db(self.db_path)

    def tearDown(self):
        try:
            os.remove(self.db_path)
        except OSError:
            pass

    def test_buyer_profile_creation_and_preference_save(self):
        """Profile created automatically and preferences stored with constraints."""
        profile = get_buyer_profile("buyer_test_1", db_path=self.db_path)
        self.assertEqual(profile["buyer_id"], "buyer_test_1")

        prefs = BuyerPreferences(
            priority_type=BuyerPreferencePriority.FASTEST_DELIVERY,
            constraints=BuyerConstraints(max_delivery_days=7, min_warranty_months=12),
        )
        saved = save_buyer_preferences("buyer_test_1", prefs, db_path=self.db_path)
        self.assertEqual(saved.priority_type, BuyerPreferencePriority.FASTEST_DELIVERY)

        retrieved = get_saved_buyer_preferences("buyer_test_1", db_path=self.db_path)
        self.assertEqual(retrieved.priority_type, BuyerPreferencePriority.FASTEST_DELIVERY)
        self.assertEqual(retrieved.constraints.max_delivery_days, 7)
        self.assertEqual(retrieved.constraints.min_warranty_months, 12)

    def test_preference_precedence_hierarchy(self):
        """Precedence: session explicit > saved explicit > approved learning > system default."""
        # 1. Default when nothing saved
        resolved = resolve_effective_preferences(buyer_id="new_buyer", db_path=self.db_path)
        self.assertEqual(resolved.priority_type, BuyerPreferencePriority.BALANCED_VALUE)

        # 2. Saved explicit preferences
        saved_prefs = BuyerPreferences(priority_type=BuyerPreferencePriority.QUALITY_WARRANTY)
        save_buyer_preferences("hierarchy_buyer", saved_prefs, source="explicit", db_path=self.db_path)
        resolved2 = resolve_effective_preferences(buyer_id="hierarchy_buyer", db_path=self.db_path)
        self.assertEqual(resolved2.priority_type, BuyerPreferencePriority.QUALITY_WARRANTY)

        # 3. Session override overrides saved
        session_prefs = BuyerPreferences(
            priority_type=BuyerPreferencePriority.LOWEST_PRICE,
            source="explicit",
        )
        resolved3 = resolve_effective_preferences(session_prefs, buyer_id="hierarchy_buyer", db_path=self.db_path)
        self.assertEqual(resolved3.priority_type, BuyerPreferencePriority.LOWEST_PRICE)

    def test_mandatory_constraints_disqualify_supplier_offer(self):
        """Offers violating hard constraints must be flagged as disqualified."""
        prefs = BuyerPreferences(
            priority_type=BuyerPreferencePriority.BALANCED_VALUE,
            constraints=BuyerConstraints(max_delivery_days=5, min_warranty_months=18),
        )

        violating_offer = CommercialOffer(
            unit_price=40.0,
            total_price=4000.0,
            quantity=100,
            delivery_days=10,  # Exceeds max 5 days
            warranty_months=12,  # Below min 18 months
            shipping_cost=50.0,
            payment_terms="Net 30",
        )

        breakdown = score_offer(violating_offer, budget_cap=50.0, reliability_score=0.9, preferences=prefs)
        self.assertTrue(breakdown.is_disqualified)
        self.assertEqual(len(breakdown.constraint_violations), 2)
        self.assertIn("exceeds mandatory max of 5 days", breakdown.constraint_violations[0])

    def test_behavioral_learning_and_recommendation_consent(self):
        """Analyzes history in Python, creates pending recommendation, activated on consent."""
        buyer_id = "frequent_buyer"
        # Seed 4 completed sessions where Vanguard (fast delivery) won
        for i in range(4):
            sess_id = f"sess-{i}"
            order_id = f"SIM-{i}"
            record_session(
                session_id=sess_id,
                product="Hydraulic Pumps",
                qty=10,
                budget=200.0,
                status="completed",
                buyer_id=buyer_id,
                db_path=self.db_path,
            )
            record_negotiation_attempt(
                session_id=sess_id,
                supplier_name="Vanguard Logistics & Supply",
                rounds=2,
                outcome="won",
                final_unit_price=180.0,
                final_total_price=1800.0,
                db_path=self.db_path,
            )
            record_deal_accepted(
                session_id=sess_id,
                order_id=order_id,
                supplier_name="Vanguard Logistics & Supply",
                amount=1800.0,
                payment_mode="SIMULATED",
                db_path=self.db_path,
            )
            record_payment_captured(order_id, db_path=self.db_path)

        # Check pending recommendations
        recs = get_pending_recommendations(buyer_id=buyer_id, db_path=self.db_path)
        self.assertTrue(len(recs) >= 1)
        delivery_rec = next(r for r in recs if r["recommendation_type"] == "priority_delivery")
        self.assertEqual(delivery_rec["suggested_priority"], BuyerPreferencePriority.FASTEST_DELIVERY.value)

        # Accept recommendation with explicit consent
        updated_prefs = accept_recommendation(delivery_rec["id"], buyer_id=buyer_id, db_path=self.db_path)
        self.assertEqual(updated_prefs.priority_type, BuyerPreferencePriority.FASTEST_DELIVERY)
        self.assertEqual(updated_prefs.source, "approved_learning")


if __name__ == "__main__":
    unittest.main()
