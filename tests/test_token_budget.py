"""Tests for token-efficiency, call budgeting, and rate-limit controls (Feature 9)."""
import unittest
from backend.groq_client import BudgetExhaustedError, GroqClient
from backend.config import settings


class TestTokenBudget(unittest.TestCase):
    def test_call_budget_enforcement(self):
        """Verify client raises BudgetExhaustedError when session call count is reached."""
        client = GroqClient(api_key="dummy_key")
        # Artificially set total_calls to MAX_MODEL_CALLS
        client.total_calls = settings.MAX_MODEL_CALLS

        with self.assertRaises(BudgetExhaustedError):
            client.check_budget("Buyer Agent")

    def test_session_token_budget_enforcement(self):
        """Verify client raises BudgetExhaustedError when token budget is exhausted."""
        client = GroqClient(api_key="dummy_key")
        client.total_tokens = settings.MAX_SESSION_TOKENS

        with self.assertRaises(BudgetExhaustedError):
            client.check_budget("Buyer Agent")

    def test_per_agent_call_bounding(self):
        """Verify individual agent call count limit is enforced."""
        client = GroqClient(api_key="dummy_key")
        agent = "Seller A"
        client.agent_calls[agent] = settings.MAX_AGENT_CALLS

        with self.assertRaises(BudgetExhaustedError):
            client.check_budget(agent)


if __name__ == "__main__":
    unittest.main()
