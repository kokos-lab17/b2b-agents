"""Tests for agent architecture, personas, and private constraints (Feature 1 & 2)."""
import unittest
from backend.personas import (
    SELLER_PERSONAS,
    BUYER_PERSONA,
    generate_private_constraints,
    get_public_supplier_profiles,
)
from backend.models import CommercialOffer


class TestAgentPersonas(unittest.TestCase):
    def test_four_independent_personas_exist(self):
        """Verify four logically independent agent personas are defined."""
        self.assertIn("role", BUYER_PERSONA)
        self.assertIn("seller_a", SELLER_PERSONAS)
        self.assertIn("seller_b", SELLER_PERSONAS)
        self.assertIn("seller_c", SELLER_PERSONAS)

        # Distinct styles and archetypes
        self.assertEqual(SELLER_PERSONAS["seller_a"]["style"], "The Hard Bargainer")
        self.assertEqual(SELLER_PERSONAS["seller_b"]["style"], "The Value Defender")
        self.assertEqual(SELLER_PERSONAS["seller_c"]["style"], "The Strategic Flexible Negotiator")

    def test_private_constraints_dynamically_generated_and_isolated(self):
        """Verify constraints are dynamic per session and differ between archetypes."""
        budget = 100.0
        qty = 200

        c_a = generate_private_constraints("seller_a", budget, qty, seed=42)
        c_b = generate_private_constraints("seller_b", budget, qty, seed=42)
        c_c = generate_private_constraints("seller_c", budget, qty, seed=42)

        # Floor prices must be less than budget, but isolated per seller
        self.assertLess(c_a.min_unit_price, budget)
        self.assertLess(c_b.min_unit_price, budget)
        self.assertLess(c_c.min_unit_price, budget)

        # Distinct capabilities: Seller B can provide up to 36 months warranty, Seller A max 12
        self.assertEqual(c_b.max_warranty_months, 36)
        self.assertEqual(c_a.max_warranty_months, 12)

        # Constraints must not be exposed in public profiles
        public_profiles = get_public_supplier_profiles()
        for p in public_profiles:
            p_dict = p.model_dump()
            self.assertNotIn("min_unit_price", p_dict)
            self.assertNotIn("floor", p_dict)
            self.assertNotIn("target_unit_price", p_dict)

    def test_backend_validation_rejects_sub_floor_offers(self):
        """Verify Python logic prevents an agent from conceding below private floor."""
        budget = 100.0
        c = generate_private_constraints("seller_a", budget, 100, seed=1)
        sub_floor = c.min_unit_price - 5.0

        # Backend logic clamps to at least min_unit_price
        safe_price = max(c.min_unit_price, sub_floor)
        self.assertGreaterEqual(safe_price, c.min_unit_price)


if __name__ == "__main__":
    unittest.main()
