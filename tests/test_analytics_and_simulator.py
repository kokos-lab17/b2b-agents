"""Unit tests for What-If Simulator, Quote Comparison, and Intelligence Analytics (Requirements 6, 8, 9, 14)."""
import unittest
from fastapi.testclient import TestClient

from backend.models import (
    BuyerConstraints,
    BuyerPreferencePriority,
    BuyerPreferences,
    QuoteComparisonItem,
    QuoteComparisonRequest,
    WhatIfSimulationRequest,
)
from backend.quotes import compare_quotes
from backend.simulator import run_what_if_simulation
from main import app


class TestAnalyticsAndSimulator(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_what_if_simulator_deterministic_execution(self):
        """What-if simulation runs across all 3 seller personas with zero external API calls."""
        req = WhatIfSimulationRequest(
            product="Ball Bearings",
            qty=1000,
            budget=15.0,
            priority_type=BuyerPreferencePriority.FASTEST_DELIVERY,
            max_delivery_days=14,
        )
        result = run_what_if_simulation(req)
        self.assertEqual(len(result.suppliers), 3)
        self.assertIsNotNone(result.recommended_winner)
        self.assertTrue(result.estimated_total_cost > 0)
        self.assertTrue(any(s["commercial_score"] > 0 for s in result.suppliers))

    def test_quote_comparison_engine(self):
        """Quote comparison evaluates quotes against constraints and ranks commercial viability."""
        req = QuoteComparisonRequest(
            product="Steel Tubing",
            budget_cap=50.0,
            preferences=BuyerPreferences(
                priority_type=BuyerPreferencePriority.LOWEST_PRICE,
                constraints=BuyerConstraints(max_delivery_days=10),
            ),
            quotes=[
                QuoteComparisonItem(
                    supplier_name="Vendor A",
                    unit_price=42.0,
                    quantity=100,
                    delivery_days=8,
                    warranty_months=12,
                    shipping_cost=50.0,
                    payment_terms="Net 30",
                ),
                QuoteComparisonItem(
                    supplier_name="Vendor B (Slow)",
                    unit_price=38.0,
                    quantity=100,
                    delivery_days=15,  # Exceeds max 10 days
                    warranty_months=12,
                    shipping_cost=50.0,
                    payment_terms="Net 30",
                ),
            ],
        )
        res = compare_quotes(req)
        self.assertEqual(len(res["comparison"]), 2)
        # Vendor B should be disqualified due to delivery constraint
        vendor_b_res = next(r for r in res["comparison"] if r["supplier_name"] == "Vendor B (Slow)")
        self.assertTrue(vendor_b_res["is_disqualified"])
        # Best qualified quote should be Vendor A
        self.assertEqual(res["best_quote"], "Vendor A")

    def test_analytics_api_endpoint(self):
        """GET /api/analytics returns procurement metrics distinguishing sandbox from simulated."""
        res = self.client.get("/api/analytics")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIn("overall", data)
        self.assertIn("suppliers", data)
        self.assertIn("strategies", data)
        overall = data["overall"]
        self.assertIn("total_sandbox_spend", overall)
        self.assertIn("total_simulated_spend", overall)
        self.assertIn("total_savings", overall)


if __name__ == "__main__":
    unittest.main()
