"""Tests for deterministic multi-factor commercial scoring engine (Feature 4)."""
import unittest
from backend.models import CommercialOffer
from backend.scoring import score_offer


class TestCommercialScoring(unittest.TestCase):
    def test_cheaper_offer_does_not_always_win(self):
        """Verify that an offer with slightly higher price can win if it provides
        superior warranty, free shipping, faster delivery, and Net 60 payment terms.
        """
        budget_cap = 100.0

        # Offer 1: Cheap ($80/unit), but slow delivery (28 days), low warranty (3 mos), expensive shipping ($350), prepaid terms
        cheap_offer = CommercialOffer(
            unit_price=80.0,
            total_price=80.0 * 100 + 350.0,
            quantity=100,
            delivery_days=28,
            warranty_months=3,
            shipping_cost=350.0,
            payment_terms="Prepaid",
        )
        score_cheap = score_offer(cheap_offer, budget_cap=budget_cap, reliability_score=0.80)

        # Offer 2: Slightly more expensive ($84/unit), but fast delivery (4 days), long warranty (24 mos), FREE shipping ($0), Net 60 terms
        better_value_offer = CommercialOffer(
            unit_price=84.0,
            total_price=84.0 * 100,
            quantity=100,
            delivery_days=4,
            warranty_months=24,
            shipping_cost=0.0,
            payment_terms="Net 60",
        )
        score_better = score_offer(better_value_offer, budget_cap=budget_cap, reliability_score=0.95)

        # Better value offer must have a higher commercial score despite higher unit price!
        self.assertGreater(score_better.total_score, score_cheap.total_score)
        self.assertGreater(score_better.warranty_score, score_cheap.warranty_score)
        self.assertGreater(score_better.shipping_score, score_cheap.shipping_score)
        self.assertGreater(score_better.terms_score, score_cheap.terms_score)

    def test_score_breakdown_ranges(self):
        """Verify individual factor scores remain strictly bounded by weights."""
        offer = CommercialOffer(
            unit_price=90.0,
            total_price=9000.0,
            quantity=100,
            delivery_days=10,
            warranty_months=12,
            shipping_cost=50.0,
            payment_terms="Net 30",
        )
        breakdown = score_offer(offer, budget_cap=100.0)

        self.assertGreaterEqual(breakdown.price_score, 0)
        self.assertLessEqual(breakdown.price_score, 40.0)

        self.assertGreaterEqual(breakdown.delivery_score, 0)
        self.assertLessEqual(breakdown.delivery_score, 15.0)

        self.assertGreaterEqual(breakdown.warranty_score, 0)
        self.assertLessEqual(breakdown.warranty_score, 15.0)

        self.assertGreaterEqual(breakdown.shipping_score, 0)
        self.assertLessEqual(breakdown.shipping_score, 10.0)

        self.assertGreaterEqual(breakdown.terms_score, 0)
        self.assertLessEqual(breakdown.terms_score, 10.0)

        self.assertGreaterEqual(breakdown.reliability_score, 0)
        self.assertLessEqual(breakdown.reliability_score, 10.0)

        self.assertGreaterEqual(breakdown.total_score, 0)
        self.assertLessEqual(breakdown.total_score, 100.0)


if __name__ == "__main__":
    unittest.main()
