"""Unit tests for Negotiation & Payment Mode separation (Requirement 3)."""
import unittest
from unittest.mock import MagicMock, patch

from backend.config import Settings
from backend.models import CommercialOffer, NegotiationMode, PaymentMode
from backend.negotiation import NegotiationEngine
from backend.paypal import PayPalError


class TestNegotiationModes(unittest.TestCase):
    def test_demo_simulated_mode_zero_external_calls(self):
        """DEMO + SIMULATED mode must make zero Groq and zero PayPal calls."""
        engine = NegotiationEngine(
            product="Industrial Valves",
            qty=50,
            budget=100.0,
            negotiation_mode=NegotiationMode.DEMO,
            payment_mode=PaymentMode.SIMULATED,
        )
        self.assertIsNone(engine.groq_client)
        result = engine.run()
        self.assertEqual(result["status"], "awaiting_approval")
        self.assertEqual(result["payment_mode"], "SIMULATED")
        self.assertEqual(result["negotiation_mode"], "DEMO")
        self.assertTrue(result["order_id"].startswith(("SIM-", "DEMO-")))
        self.assertIsNone(result["approve_url"])  # No fake PayPal URLs!

    @patch("backend.negotiation.PayPalClient.create_order")
    def test_demo_paypal_sandbox_mode(self, mock_create_order):
        """DEMO + PAYPAL_SANDBOX mode uses deterministic negotiation with real PayPal order creation."""
        mock_create_order.return_value = ("8AB12345678901234", "https://www.sandbox.paypal.com/checkoutnow?token=8AB12345678901234")

        engine = NegotiationEngine(
            product="Industrial Valves",
            qty=50,
            budget=100.0,
            negotiation_mode=NegotiationMode.DEMO,
            payment_mode=PaymentMode.PAYPAL_SANDBOX,
        )
        self.assertIsNone(engine.groq_client)
        result = engine.run()
        self.assertEqual(result["status"], "awaiting_approval")
        self.assertEqual(result["payment_mode"], "PAYPAL_SANDBOX")
        self.assertEqual(result["order_id"], "8AB12345678901234")
        self.assertEqual(result["approve_url"], "https://www.sandbox.paypal.com/checkoutnow?token=8AB12345678901234")
        mock_create_order.assert_called_once()

    @patch("backend.negotiation.GroqClient.call_agent")
    @patch("backend.negotiation.settings", Settings(GROQ_API_KEY="mock_groq_key"))
    def test_live_ai_simulated_mode(self, mock_groq_call):
        """LIVE_AI + SIMULATED mode uses Groq for negotiation and simulated payment for checkout."""
        mock_groq_call.return_value = {
            "chosen_strategy": "PRICE_DISCOUNT",
            "strategy_reasoning": "Sellers show flexibility",
            "proposals": {},
        }
        engine = NegotiationEngine(
            product="Sensors",
            qty=20,
            budget=200.0,
            negotiation_mode=NegotiationMode.LIVE_AI,
            payment_mode=PaymentMode.SIMULATED,
        )
        self.assertIsNotNone(engine.groq_client)
        result = engine.run()
        self.assertEqual(result["payment_mode"], "SIMULATED")
        self.assertEqual(result["negotiation_mode"], "LIVE_AI")
        self.assertTrue(result["order_id"].startswith("SIM-"))
        self.assertIsNone(result["approve_url"])

    @patch("backend.negotiation.settings", Settings(GROQ_API_KEY=""))
    def test_live_ai_mode_without_groq_key_raises_error(self):
        """Attempting to run LIVE_AI without GROQ_API_KEY raises ValueError."""
        with self.assertRaises(ValueError) as ctx:
            NegotiationEngine(
                product="Sensors",
                qty=20,
                budget=200.0,
                negotiation_mode=NegotiationMode.LIVE_AI,
                payment_mode=PaymentMode.SIMULATED,
            )
        self.assertIn("Groq API key is not configured", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
