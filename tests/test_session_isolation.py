"""Unit tests for session isolation and concurrent procurement runs (Requirement 13)."""
import time
import unittest
from fastapi.testclient import TestClient

from main import app, _active_sessions, _lock


class TestSessionIsolation(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_session_isolation_and_scoped_status(self):
        """Two concurrent procurement sessions with distinct session_ids must not collide."""
        res1 = self.client.post("/api/start", json={
            "product": "Industrial Fasteners",
            "qty": 500,
            "budget": 5.0,
            "demo_mode": True,
            "buyer_id": "buyer_alpha",
        })
        self.assertEqual(res1.status_code, 200)
        session_id_1 = res1.json()["session_id"]

        res2 = self.client.post("/api/start", json={
            "product": "Titanium Bolts",
            "qty": 100,
            "budget": 45.0,
            "demo_mode": True,
            "buyer_id": "buyer_beta",
        })
        self.assertEqual(res2.status_code, 200)
        session_id_2 = res2.json()["session_id"]

        self.assertNotEqual(session_id_1, session_id_2)

        # Poll until both background worker threads complete
        data_1 = {}
        data_2 = {}
        for _ in range(30):
            res_s1 = self.client.get(f"/api/status?session_id={session_id_1}")
            res_s2 = self.client.get(f"/api/status?session_id={session_id_2}")
            if res_s1.status_code == 200 and res_s2.status_code == 200:
                data_1 = res_s1.json()
                data_2 = res_s2.json()
                if data_1.get("order_id") and data_2.get("order_id"):
                    break
            time.sleep(0.1)

        self.assertEqual(data_1.get("session_id"), session_id_1)
        self.assertTrue(any("Fasteners" in str(e.get("message", "")) for e in data_1.get("events", [])))

        self.assertEqual(data_2.get("session_id"), session_id_2)
        self.assertTrue(any("Titanium Bolts" in str(e.get("message", "")) for e in data_2.get("events", [])))

        # Verify distinct order IDs
        self.assertIsNotNone(data_1.get("order_id"))
        self.assertIsNotNone(data_2.get("order_id"))
        self.assertNotEqual(data_1["order_id"], data_2["order_id"])


if __name__ == "__main__":
    unittest.main()
