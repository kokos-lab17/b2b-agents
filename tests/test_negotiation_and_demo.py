"""Tests for autonomous adaptive negotiation, state-dependent strategy switching, and seller psychology (Features 1, 2, 3, 7, 8)."""
import unittest
from backend.models import CommercialOffer, BuyerProposal, PrivateSellerConstraint, validate_and_sanitize_offer
from backend.negotiation import NegotiationEngine


class TestNegotiationAndDemo(unittest.TestCase):
    def test_demo_mode_executes_deterministically_without_credentials(self):
        """Verify demo mode runs completely, produces structured events, and makes explainable decisions."""
        captured_events = []

        engine = NegotiationEngine(
            product="Heavy Duty Ball Bearings",
            qty=500,
            budget=45.0,
            demo_mode=True,
            on_event=lambda e: captured_events.append(e),
        )
        result = engine.run()

        # Terminal status awaiting approval or completed
        self.assertIn(result["status"], ("awaiting_approval", "completed"))
        self.assertIsNotNone(result["deal"])
        self.assertIn("selected_vendor", result["deal"])

        # Order ID must start with DEMO-
        self.assertTrue(result["order_id"].startswith("DEMO-"))

        # Check events
        self.assertGreater(len(captured_events), 5)
        # All demo events must be visibly marked as simulation
        for evt in captured_events:
            self.assertTrue(evt["message"].startswith("[DEMO SIMULATION]"))

    def test_buyer_strategy_choice_depends_on_actual_seller_responses(self):
        """Verify buyer strategy changes dynamically depend on actual seller responses,
        not a predetermined sequence (Feature 2 & 8).
        """
        engine = NegotiationEngine(
            product="Test Component",
            qty=100,
            budget=100.0,
            demo_mode=True,
        )

        # Scenario A: Sellers resist price cuts (lowest offer is $95/unit, only 5% below $100 budget)
        resisting_offers = {
            "seller_a": CommercialOffer(unit_price=95.0, total_price=9600.0, quantity=100, delivery_days=10, warranty_months=12, shipping_cost=100.0, payment_terms="Net 30"),
            "seller_b": CommercialOffer(unit_price=98.0, total_price=9880.0, quantity=100, delivery_days=14, warranty_months=18, shipping_cost=80.0, payment_terms="Net 30"),
            "seller_c": CommercialOffer(unit_price=96.0, total_price=9700.0, quantity=100, delivery_days=7, warranty_months=12, shipping_cost=100.0, payment_terms="Net 30"),
        }
        decision_resistant = engine._buyer_evaluate_and_decide(resisting_offers)
        # Buyer must detect price resistance and pivot to non-price variables
        self.assertEqual(decision_resistant["chosen_strategy"], "WARRANTY_EXPANSION_AND_FREIGHT_CONCESSION")
        self.assertIn("firm resistance", decision_resistant["strategy_reasoning"].lower())

        # Scenario B: Sellers show significant price flexibility (lowest offer is $82/unit, 18% below $100 budget)
        flexible_offers = {
            "seller_a": CommercialOffer(unit_price=85.0, total_price=8600.0, quantity=100, delivery_days=10, warranty_months=12, shipping_cost=100.0, payment_terms="Net 30"),
            "seller_b": CommercialOffer(unit_price=88.0, total_price=8880.0, quantity=100, delivery_days=14, warranty_months=18, shipping_cost=80.0, payment_terms="Net 30"),
            "seller_c": CommercialOffer(unit_price=82.0, total_price=8300.0, quantity=100, delivery_days=7, warranty_months=12, shipping_cost=100.0, payment_terms="Net 30"),
        }
        decision_flexible = engine._buyer_evaluate_and_decide(flexible_offers)
        # Buyer continues with PRICE_DISCOUNT because sellers demonstrated room to cut
        self.assertEqual(decision_flexible["chosen_strategy"], "PRICE_DISCOUNT")
        self.assertIn("flexibility", decision_flexible["strategy_reasoning"].lower())

    def test_hard_bargainer_seller_resists_without_volume_commitment(self):
        """Verify Seller A genuinely resists price cuts unless buyer provides a volume commitment (Feature 3)."""
        engine = NegotiationEngine(
            product="Structural Steel",
            qty=100,
            budget=100.0,
            demo_mode=True,
        )
        r1_offer = CommercialOffer(
            unit_price=95.0,
            total_price=9600.0,
            quantity=100,
            delivery_days=10,
            warranty_months=12,
            shipping_cost=100.0,
            payment_terms="Net 30",
        )

        # Case 1: Buyer asks for price discount WITHOUT offering volume commitment
        weak_proposal = BuyerProposal(
            target_unit_price=85.0,
            target_delivery_days=10,
            target_warranty_months=12,
            target_shipping_cost=50.0,
            target_payment_terms="Net 30",
            rationale="Just lower your price please.",
            concession_requested="Price discount",
        )
        r2_response = engine._seller_round2_decision("seller_a", r1_offer, weak_proposal, "PRICE_DISCOUNT")
        # Hard bargainer MUST hold firm on unit price!
        self.assertEqual(r2_response.unit_price, r1_offer.unit_price)
        self.assertIn("Volume Commitment Required", r2_response.special_concessions[0])

        # Case 2: Buyer offers explicit volume commitment
        strong_proposal = BuyerProposal(
            target_unit_price=85.0,
            target_delivery_days=10,
            target_warranty_months=12,
            target_shipping_cost=50.0,
            target_payment_terms="Net 30",
            rationale="We offer a repeat volume commitment of 4 annual orders.",
            concession_requested="Volume rebate",
        )
        r2_concession = engine._seller_round2_decision("seller_a", r1_offer, strong_proposal, "PRICE_DISCOUNT")
        # Hard bargainer softens when volume commitment is given!
        self.assertLess(r2_concession.unit_price, r1_offer.unit_price)
        self.assertIn("Volume Rebate", r2_concession.special_concessions[0])

    def test_python_validation_enforces_all_five_dimensions(self):
        """Verify Python logic validates price floor, delivery, warranty, shipping, and payment terms (Feature 4)."""
        constraints = PrivateSellerConstraint(
            min_unit_price=80.0,
            target_unit_price=90.0,
            min_delivery_days=7,
            max_warranty_months=24,
            min_shipping_cost=50.0,
            allowed_payment_terms=["Net 30", "Net 60"],
        )

        # Proposed offer violates all 5 dimensions:
        # Price too low (70 < 80)
        # Delivery too fast (3 < 7)
        # Warranty too long (36 > 24)
        # Shipping too low (0 < 50)
        # Payment terms not allowed ("Cryptocurrency")
        bad_offer = CommercialOffer(
            unit_price=70.0,
            total_price=7000.0,
            quantity=100,
            delivery_days=3,
            warranty_months=36,
            shipping_cost=0.0,
            payment_terms="Cryptocurrency",
        )

        validated = validate_and_sanitize_offer(bad_offer, constraints, qty=100)

        # Enforced bounds
        self.assertEqual(validated.unit_price, 80.0)  # Clamped to min floor
        self.assertEqual(validated.delivery_days, 7)   # Clamped to min delivery
        self.assertEqual(validated.warranty_months, 24) # Clamped to max warranty
        self.assertEqual(validated.shipping_cost, 50.0) # Clamped to min shipping
        self.assertEqual(validated.payment_terms, "Net 30") # Clamped to allowed terms
        # Total price deterministically recomputed
        self.assertEqual(validated.total_price, 80.0 * 100 + 50.0)


if __name__ == "__main__":
    unittest.main()
