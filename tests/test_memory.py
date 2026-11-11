"""Tests for supplier persistent memory and transaction separation (Feature 5)."""
import os
import tempfile
import unittest
from backend.memory import (
    ensure_supplier_profile,
    get_supplier_metrics,
    init_db,
    record_deal_accepted,
    record_negotiation_attempt,
    record_payment_captured,
    record_session,
)


class TestSupplierMemory(unittest.TestCase):
    def setUp(self):
        self.tmp_fd, self.db_path = tempfile.mkstemp(suffix=".db")
        init_db(self.db_path)

    def tearDown(self):
        os.close(self.tmp_fd)
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def test_negotiation_attempt_is_not_marked_as_paid(self):
        """Negotiation attempt must be recorded without marking as accepted or paid."""
        supplier = "Apex Industrial Corp"
        ensure_supplier_profile(supplier, "Volume", "Hard", "Bulk", db_path=self.db_path)

        record_session("s1", "Widget", 50, 100.0, db_path=self.db_path)
        record_negotiation_attempt("s1", supplier, 2, "countered", 85.0, 4250.0, db_path=self.db_path)

        metrics = get_supplier_metrics(supplier, db_path=self.db_path)
        self.assertEqual(metrics["attempted_deals"], 1)
        self.assertEqual(metrics["accepted_deals"], 0)
        self.assertEqual(metrics["captured_payments"], 0)

    def test_accepted_deal_is_not_marked_as_captured_until_payment(self):
        """Accepted deal creates an order but must NOT be marked as captured until verified."""
        supplier = "Sterling Quality Works"
        order_id = "8AB12345678901234"
        ensure_supplier_profile(supplier, "Value", "Defender", "Precision", db_path=self.db_path)

        record_session("s2", "Sensor", 100, 50.0, db_path=self.db_path)
        record_deal_accepted("s2", order_id, supplier, 4500.0, db_path=self.db_path)

        metrics = get_supplier_metrics(supplier, db_path=self.db_path)
        self.assertEqual(metrics["accepted_deals"], 1)
        self.assertEqual(metrics["captured_payments"], 0)  # NOT captured yet!

        # Now simulate payment capture
        captured = record_payment_captured(order_id, db_path=self.db_path)
        self.assertTrue(captured)

        # Now metrics must reflect captured payment
        updated_metrics = get_supplier_metrics(supplier, db_path=self.db_path)
        self.assertEqual(updated_metrics["captured_payments"], 1)


if __name__ == "__main__":
    unittest.main()
