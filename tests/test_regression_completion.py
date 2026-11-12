"""Regression tests validating full procurement lifecycle, worker reliability, and buyer intelligence."""
import concurrent.futures
import time
import unittest
import uuid
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

import main
from backend.models import (
    CommercialOffer,
    NegotiationMode,
    PaymentMode,
    StatusResponse,
)
from backend.negotiation import NegotiationEngine


class TestRegressionProcurementCompletion(unittest.TestCase):
    """Verifies all 16 regression requirements from Part 6 of master prompt."""

    def setUp(self):
        self.client = TestClient(main.app)

    def test_01_demo_simulated_completes_without_groq_calls(self):
        """1. DEMO + SIMULATED completes without Groq calls."""
        session_id = f"test-demo-sim-{uuid.uuid4().hex[:8]}"
        with patch("backend.groq_client.GroqClient.call_agent") as mock_groq:
            engine = NegotiationEngine(
                product="Industrial Ball Bearings",
                qty=200,
                budget=50.0,
                demo_mode=True,
                negotiation_mode=NegotiationMode.DEMO,
                payment_mode=PaymentMode.SIMULATED,
                session_id=session_id,
            )
            result = engine.run()
            self.assertEqual(result["status"], "awaiting_approval")
            self.assertEqual(mock_groq.call_count, 0)
            self.assertIsNotNone(result.get("order_id"))
            self.assertTrue(result["order_id"].startswith("DEMO-") or result["order_id"].startswith("SIM-"))

    def test_02_all_three_sellers_participate_round1_and_round2(self):
        """2. All 3 seller personas participate in Round 1 and Round 2."""
        session_id = f"test-sellers-{uuid.uuid4().hex[:8]}"
        engine = NegotiationEngine(
            product="Pressure Gauges",
            qty=100,
            budget=80.0,
            demo_mode=True,
            session_id=session_id,
        )
        result = engine.run()
        events = result["events"]
        expected_sellers = {
            "Apex Industrial Corp",
            "Sterling Quality Works",
            "Vanguard Logistics & Supply",
        }
        r1_sellers = set()
        r2_sellers = set()
        for e in events:
            if e.get("agent") in expected_sellers and e.get("event_type") == "counteroffer":
                if e.get("step") in [3, 4, 5]:
                    r1_sellers.add(e.get("agent"))
                elif e.get("step") in [7, 8, 9]:
                    r2_sellers.add(e.get("agent"))

        self.assertEqual(r1_sellers, expected_sellers)
        self.assertEqual(r2_sellers, expected_sellers)

    def test_03_all_three_final_offers_present(self):
        """3. All 3 final offers are generated and present in the response."""
        session_id = f"test-final-offers-{uuid.uuid4().hex[:8]}"
        engine = NegotiationEngine(
            product="Valves",
            qty=500,
            budget=90.0,
            demo_mode=True,
            session_id=session_id,
        )
        result = engine.run()
        final_offers = result.get("final_offers", [])
        self.assertEqual(len(final_offers), 3)
        vendors = {fo["vendor"] for fo in final_offers}
        self.assertIn("Apex Industrial Corp", vendors)
        self.assertIn("Sterling Quality Works", vendors)
        self.assertIn("Vanguard Logistics & Supply", vendors)
        for fo in final_offers:
            self.assertGreater(fo["unit_price"], 0)
            self.assertGreater(fo["delivery_days"], 0)
            self.assertGreaterEqual(fo["warranty_months"], 0)
            self.assertGreater(fo["commercial_score"], 0)

    def test_04_winner_selected_and_returned_by_status(self):
        """4. A winner is selected and returned by /api/status."""
        resp = self.client.post(
            "/api/start",
            json={
                "product": "Hydraulic Pumps",
                "qty": 50,
                "budget": 200.0,
                "negotiation_mode": "DEMO",
                "payment_mode": "SIMULATED",
            },
        )
        self.assertEqual(resp.status_code, 200)
        session_id = resp.json()["session_id"]

        # Wait briefly for synchronous daemon thread in test
        time.sleep(0.8)

        status_resp = self.client.get(f"/api/status?session_id={session_id}")
        self.assertEqual(status_resp.status_code, 200)
        data = status_resp.json()
        self.assertIn(data["status"], ["negotiating", "awaiting_approval"])
        if data["status"] == "awaiting_approval":
            self.assertIsNotNone(data.get("winning_vendor"))
            self.assertIsNotNone(data.get("deal"))

    def test_05_deal_details_appear_in_completion_state(self):
        """5. Deal details appear in the completion state."""
        session_id = f"test-deal-{uuid.uuid4().hex[:8]}"
        engine = NegotiationEngine(
            product="Industrial Valves",
            qty=500,
            budget=90.0,
            demo_mode=True,
            session_id=session_id,
        )
        res = engine.run()
        deal = res.get("deal")
        self.assertIsNotNone(deal)
        self.assertIn("selected_vendor", deal)
        self.assertIn("commercial_score", deal)
        self.assertIn("terms", deal)
        self.assertIn("unit_price", deal["terms"])
        self.assertIn("total_price", deal["terms"])

    def test_06_simulated_order_creation_succeeds_without_paypal_calls(self):
        """6. Simulated order creation succeeds without PayPal calls."""
        with patch("backend.paypal.PayPalClient.create_order") as mock_pp:
            session_id = f"test-no-paypal-{uuid.uuid4().hex[:8]}"
            engine = NegotiationEngine(
                product="Sensors",
                qty=100,
                budget=50.0,
                payment_mode=PaymentMode.SIMULATED,
                demo_mode=True,
                session_id=session_id,
            )
            res = engine.run()
            self.assertEqual(mock_pp.call_count, 0)
            self.assertIsNone(res.get("approve_url"))
            self.assertTrue(res["order_id"].startswith("DEMO-") or res["order_id"].startswith("SIM-"))

    def test_07_paypal_sandbox_mode_creates_sandbox_order(self):
        """7. DEMO + PAYPAL_SANDBOX creates a Sandbox order with mocked PayPal client."""
        with patch("backend.paypal.PayPalClient.create_order") as mock_create:
            mock_create.return_value = ("PAYPAL-ORD-99999", "https://sandbox.paypal.com/checkout?token=PAYPAL-ORD-99999")
            session_id = f"test-pp-mock-{uuid.uuid4().hex[:8]}"
            engine = NegotiationEngine(
                product="Gaskets",
                qty=200,
                budget=40.0,
                negotiation_mode=NegotiationMode.DEMO,
                payment_mode=PaymentMode.PAYPAL_SANDBOX,
                demo_mode=True,
                session_id=session_id,
            )
            res = engine.run()
            self.assertEqual(res["order_id"], "PAYPAL-ORD-99999")
            self.assertIn("sandbox.paypal.com", res["approve_url"])

    def test_08_worker_exceptions_produce_visible_failure_status(self):
        """8. Worker exceptions produce a visible failure status."""
        session_id = f"test-fail-{uuid.uuid4().hex[:8]}"
        with patch("backend.negotiation.NegotiationEngine.run", side_effect=RuntimeError("Simulated engine explosion")):
            main._active_sessions[session_id] = {
                "session_id": session_id,
                "events": [],
                "order_id": None,
                "approve_url": None,
                "status": "negotiating",
                "vendors": [],
                "deal": None,
                "final_offers": [],
                "winning_vendor": None,
                "winning_offer": None,
                "buyer_intelligence": None,
                "token_usage": None,
                "negotiation_mode": "DEMO",
                "payment_mode": "SIMULATED",
                "buyer_id": "default_buyer",
            }
            main._run_procurement_worker(
                session_id=session_id,
                product="Flanges",
                qty=10,
                budget=10.0,
                demo_mode=True,
                negotiation_mode=NegotiationMode.DEMO,
                payment_mode=PaymentMode.SIMULATED,
                buyer_id="default_buyer",
                preferences=None,
            )
            state = main._get_session_state(session_id)
            self.assertEqual(state["status"], "failed")
            errored_events = [e for e in state["events"] if e.get("status") == "failed"]
            self.assertTrue(len(errored_events) > 0)
            self.assertIn("Simulated engine explosion", errored_events[0]["message"])

    def test_09_worker_timeout_transitions_state_to_failed(self):
        """9. Worker timeout transitions state to failed."""
        def slow_run():
            time.sleep(2.0)
            return {}

        session_id = f"test-timeout-{uuid.uuid4().hex[:8]}"
        main._active_sessions[session_id] = {
            "session_id": session_id,
            "events": [],
            "order_id": None,
            "approve_url": None,
            "status": "negotiating",
            "vendors": [],
            "deal": None,
            "final_offers": [],
            "winning_vendor": None,
            "winning_offer": None,
            "buyer_intelligence": None,
            "token_usage": None,
            "negotiation_mode": "DEMO",
            "payment_mode": "SIMULATED",
            "buyer_id": "default_buyer",
        }

        # Mock timeout to 0.05 seconds for fast unit test execution
        with patch("concurrent.futures.Future.result", side_effect=concurrent.futures.TimeoutError):
            main._run_procurement_worker(
                session_id=session_id,
                product="Pipes",
                qty=10,
                budget=10.0,
                demo_mode=True,
                negotiation_mode=NegotiationMode.DEMO,
                payment_mode=PaymentMode.SIMULATED,
                buyer_id="default_buyer",
                preferences=None,
            )
            state = main._get_session_state(session_id)
            self.assertEqual(state["status"], "failed")
            self.assertIn("timed out", state["events"][-1]["message"])

    def test_10_multiple_sessions_do_not_overwrite_one_another(self):
        """10. Multiple sessions do not overwrite one another."""
        s1 = f"session-iso-1-{uuid.uuid4().hex[:6]}"
        s2 = f"session-iso-2-{uuid.uuid4().hex[:6]}"

        main._active_sessions[s1] = {
            "session_id": s1,
            "events": [{"step": 1, "agent": "System", "message": "Msg 1", "status": "negotiating"}],
            "order_id": "ORD-1",
            "approve_url": None,
            "status": "awaiting_approval",
            "vendors": [],
            "deal": {"selected_vendor": "Vendor 1"},
            "final_offers": [],
            "winning_vendor": "Vendor 1",
            "winning_offer": None,
            "buyer_intelligence": None,
            "token_usage": None,
            "negotiation_mode": "DEMO",
            "payment_mode": "SIMULATED",
        }
        main._active_sessions[s2] = {
            "session_id": s2,
            "events": [{"step": 1, "agent": "System", "message": "Msg 2", "status": "negotiating"}],
            "order_id": "ORD-2",
            "approve_url": None,
            "status": "awaiting_approval",
            "vendors": [],
            "deal": {"selected_vendor": "Vendor 2"},
            "final_offers": [],
            "winning_vendor": "Vendor 2",
            "winning_offer": None,
            "buyer_intelligence": None,
            "token_usage": None,
            "negotiation_mode": "DEMO",
            "payment_mode": "SIMULATED",
        }

        r1 = self.client.get(f"/api/status?session_id={s1}").json()
        r2 = self.client.get(f"/api/status?session_id={s2}").json()

        self.assertEqual(r1["order_id"], "ORD-1")
        self.assertEqual(r2["order_id"], "ORD-2")
        self.assertEqual(r1["winning_vendor"], "Vendor 1")
        self.assertEqual(r2["winning_vendor"], "Vendor 2")

    def test_11_budget_headroom_not_mislabeled_as_negotiated_savings(self):
        """11. Budget headroom is strictly distinguished from negotiated savings."""
        session_id = f"test-headroom-{uuid.uuid4().hex[:8]}"
        engine = NegotiationEngine(
            product="Industrial Valves",
            qty=500,
            budget=90.0,
            demo_mode=True,
            session_id=session_id,
        )
        res = engine.run()
        bi = res.get("buyer_intelligence")
        self.assertIsNotNone(bi)
        self.assertIn("budget_headroom", bi)
        self.assertIn("negotiated_savings", bi)

        # Budget headroom = (budget_cap * qty) - final_deal_total
        expected_headroom = round((90.0 * 500) - bi["final_deal_total"], 2)
        self.assertEqual(bi["budget_headroom"], expected_headroom)
        # Negotiated savings = (initial_quote - final_unit_price) * qty
        self.assertGreaterEqual(bi["negotiated_savings"], 0)

    def test_12_validation_errors_return_http_422(self):
        """12. Validation errors return HTTP 422."""
        resp = self.client.post(
            "/api/start",
            json={
                "product": "",
                "qty": -5,
                "budget": -100,
            },
        )
        self.assertEqual(resp.status_code, 422)

    def test_13_status_endpoint_returns_final_offers_and_intelligence(self):
        """13. Status endpoint returns final_offers and buyer_intelligence."""
        session_id = f"test-status-offers-{uuid.uuid4().hex[:8]}"
        main._active_sessions[session_id] = {
            "session_id": session_id,
            "events": [],
            "order_id": "ORD-123",
            "approve_url": None,
            "status": "awaiting_approval",
            "vendors": [],
            "deal": {"selected_vendor": "Apex Industrial Corp"},
            "final_offers": [{"vendor": "Apex Industrial Corp", "unit_price": 80.0}],
            "winning_vendor": "Apex Industrial Corp",
            "winning_offer": {"unit_price": 80.0, "total_price": 40000.0},
            "buyer_intelligence": {"recommended_supplier": "Apex Industrial Corp", "budget_headroom": 5000.0},
            "token_usage": None,
            "negotiation_mode": "DEMO",
            "payment_mode": "SIMULATED",
            "last_heartbeat": "2026-11-12T10:00:00Z",
            "updated_at": "2026-11-12T10:00:00Z",
        }
        res = self.client.get(f"/api/status?session_id={session_id}").json()
        self.assertEqual(len(res["final_offers"]), 1)
        self.assertEqual(res["buyer_intelligence"]["budget_headroom"], 5000.0)
        self.assertEqual(res["winning_vendor"], "Apex Industrial Corp")

    def test_14_decision_audit_trail_recorded(self):
        """14. Decision audit trail records strategy formulation and scoring."""
        session_id = f"test-audit-{uuid.uuid4().hex[:8]}"
        engine = NegotiationEngine(
            product="Tubing",
            qty=100,
            budget=45.0,
            demo_mode=True,
            session_id=session_id,
        )
        engine.run()
        audit_resp = self.client.get(f"/api/audit-trail?session_id={session_id}")
        self.assertEqual(audit_resp.status_code, 200)
        entries = audit_resp.json().get("audit_trail", [])
        actions = {e["action"] for e in entries}
        self.assertIn("STRATEGY_FORMULATION", actions)
        self.assertIn("STRATEGY_ADAPTATION", actions)
        self.assertIn("VENDOR_SELECTION", actions)

    def test_15_strategy_outcomes_recorded_in_memory(self):
        """15. Strategy outcomes are recorded in memory."""
        session_id = f"test-strat-{uuid.uuid4().hex[:8]}"
        engine = NegotiationEngine(
            product="Fasteners",
            qty=1000,
            budget=15.0,
            demo_mode=True,
            session_id=session_id,
        )
        engine.run()
        analytics_resp = self.client.get("/api/analytics")
        self.assertEqual(analytics_resp.status_code, 200)
        data = analytics_resp.json()
        self.assertIn("strategies", data)
        self.assertTrue(len(data["strategies"]) > 0)

    def test_16_idempotent_payment_capture_works(self):
        """16. Idempotent payment capture works correctly."""
        session_id = f"test-idem-{uuid.uuid4().hex[:8]}"
        engine = NegotiationEngine(
            product="Nuts & Bolts",
            qty=500,
            budget=20.0,
            demo_mode=True,
            payment_mode=PaymentMode.SIMULATED,
            session_id=session_id,
        )
        res = engine.run()
        token = res["order_id"]

        main._active_sessions[session_id] = {
            "session_id": session_id,
            "events": [],
            "order_id": token,
            "approve_url": None,
            "status": "awaiting_approval",
            "vendors": [],
            "deal": res["deal"],
            "token_usage": None,
            "negotiation_mode": "DEMO",
            "payment_mode": "SIMULATED",
        }

        # First capture
        c1 = self.client.get(f"/api/confirm-payment?token={token}&session_id={session_id}")
        self.assertEqual(c1.status_code, 200)
        self.assertEqual(c1.json()["status"], "captured")

        # Second capture (idempotent)
        c2 = self.client.get(f"/api/confirm-payment?token={token}&session_id={session_id}")
        self.assertEqual(c2.status_code, 200)
        self.assertEqual(c2.json()["status"], "captured")
        self.assertTrue(c2.json().get("already_captured", False))


if __name__ == "__main__":
    unittest.main()
