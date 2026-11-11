"""Tests for autonomous adaptive negotiation and deterministic demo mode (Features 2, 3, 7, 8)."""
import unittest
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

        # Terminal status
        self.assertEqual(result["status"], "completed")
        self.assertIsNotNone(result["deal"])
        self.assertIn("selected_vendor", result["deal"])

        # No PayPal URL should be created in demo mode
        self.assertIsNone(result["approve_url"])
        self.assertTrue(result["order_id"].startswith("DEMO-"))

        # Check events
        self.assertGreater(len(captured_events), 5)
        # All demo events must be visibly marked as simulation
        for evt in captured_events:
            self.assertTrue(evt["message"].startswith("[DEMO SIMULATION]"))

    def test_buyer_autonomously_switches_strategy(self):
        """Verify buyer detects price resistance and switches strategy from price to warranty/freight."""
        strategy_switches = []

        def track_events(evt):
            if evt.get("event_type") == "strategy_switch":
                strategy_switches.append(evt)

        engine = NegotiationEngine(
            product="Precision Hydraulic Valves",
            qty=200,
            budget=120.0,
            demo_mode=True,
            on_event=track_events,
        )
        engine.run()

        # Buyer must have at least 2 strategy formulations: Initial PRICE_DISCOUNT then adaptation
        self.assertGreaterEqual(len(strategy_switches), 2)
        initial_strat = strategy_switches[0]["strategy"]
        adapted_strat = strategy_switches[1]["strategy"]

        self.assertEqual(initial_strat, "PRICE_DISCOUNT")
        self.assertEqual(adapted_strat, "WARRANTY_EXPANSION_AND_FREIGHT_CONCESSION")

        # Rationale must explain WHY the strategy was changed
        explanation = strategy_switches[1]["message"]
        self.assertIn("Pivoting strategy", explanation)


if __name__ == "__main__":
    unittest.main()
