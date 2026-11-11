"""Tests for PayPal sandbox flow and API endpoint backward compatibility (Features 6 & 10)."""
import unittest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from main import app
from backend.paypal import PayPalClient, PayPalError
from backend.models import CommercialOffer


class TestPayPalAndAPI(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_api_status_contract_compatibility(self):
        """GET /api/status must return expected fields preserving legacy contract."""
        response = self.client.get("/api/status")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        for field in ("events", "order_id", "approve_url", "status", "vendors"):
            self.assertIn(field, data)

    def test_api_start_contract_compatibility(self):
        """POST /api/start accepts product, qty, budget and returns {status: 'started'}."""
        payload = {
            "product": "Industrial Flanges",
            "qty": 100,
            "budget": 50.0,
            "demo_mode": True,
        }
        response = self.client.post("/api/start", json=payload)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "started"})

    def test_paypal_client_invalid_offer_rejected(self):
        """Zero or negative price offers must be rejected before making PayPal API calls."""
        client = PayPalClient(client_id="dummy", secret="dummy")
        # Construct unvalidated instance to test defense-in-depth in PayPalClient
        invalid_offer = CommercialOffer.model_construct(
            unit_price=-10.0,
            total_price=-1000.0,
            quantity=100,
            delivery_days=7,
            warranty_months=12,
            shipping_cost=0.0,
            payment_terms="Net 30",
        )
        with self.assertRaises(PayPalError):
            client.create_order("Test Item", 100, invalid_offer)

    @patch("backend.paypal.httpx.Client")
    def test_paypal_create_order_parses_order_id_and_approve_url(self, mock_client_cls):
        """Verify PayPal order creation correctly extracts order_id and approve link."""
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_client

        # Mock token response
        token_resp = MagicMock()
        token_resp.json.return_value = {"access_token": "mock_token_123"}
        token_resp.status_code = 200

        # Mock create order response
        order_resp = MagicMock()
        order_resp.json.return_value = {
            "id": "8AB99988877766655",
            "status": "CREATED",
            "links": [
                {"rel": "self", "href": "https://api-m.sandbox.paypal.com/v2/checkout/orders/8AB99988877766655"},
                {"rel": "approve", "href": "https://www.sandbox.paypal.com/checkoutnow?token=8AB99988877766655"},
            ],
        }
        order_resp.status_code = 201

        mock_client.post.side_effect = [token_resp, order_resp]

        client = PayPalClient(
            client_id="test_id",
            secret="test_secret",
            frontend_url="https://b2b-procure.netlify.app",
        )
        offer = CommercialOffer(
            unit_price=45.0,
            total_price=4500.0,
            quantity=100,
            delivery_days=7,
            warranty_months=12,
            shipping_cost=0.0,
            payment_terms="Net 30",
        )
        order_id, approve_url = client.create_order("Test Item", 100, offer)

        self.assertEqual(order_id, "8AB99988877766655")
        self.assertEqual(approve_url, "https://www.sandbox.paypal.com/checkoutnow?token=8AB99988877766655")

        # Verify application_context URLs match the configured FRONTEND_URL
        created_payload = mock_client.post.call_args_list[1].kwargs["json"]
        app_ctx = created_payload["application_context"]
        self.assertEqual(app_ctx["return_url"], "https://b2b-procure.netlify.app/?paypal_return=true")
        self.assertEqual(app_ctx["cancel_url"], "https://b2b-procure.netlify.app/?paypal_cancel=true")

    def test_paypal_create_order_raises_if_frontend_url_missing(self):
        """Missing FRONTEND_URL must raise clear PayPalError rather than silent placeholder."""
        client = PayPalClient(client_id="test_id", secret="test_secret", frontend_url="")
        offer = CommercialOffer(
            unit_price=45.0,
            total_price=4500.0,
            quantity=100,
            delivery_days=7,
            warranty_months=12,
            shipping_cost=0.0,
            payment_terms="Net 30",
        )
        with self.assertRaises(PayPalError) as ctx:
            client.create_order("Test Item", 100, offer)
        self.assertIn("FRONTEND_URL environment variable is not configured", str(ctx.exception))

    @patch("backend.paypal.httpx.Client")
    def test_paypal_capture_order_idempotent_when_already_captured(self, mock_client_cls):
        """PayPal HTTP 422 with ORDER_ALREADY_CAPTURED must return idempotent success."""
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_client

        token_resp = MagicMock()
        token_resp.json.return_value = {"access_token": "mock_token_123"}
        token_resp.status_code = 200

        # Simulate PayPal 422 with ORDER_ALREADY_CAPTURED issue
        capture_resp = MagicMock()
        capture_resp.status_code = 422
        capture_resp.text = '{"name":"UNPROCESSABLE_ENTITY","details":[{"issue":"ORDER_ALREADY_CAPTURED"}]}'
        capture_resp.json.return_value = {
            "name": "UNPROCESSABLE_ENTITY",
            "details": [{"issue": "ORDER_ALREADY_CAPTURED"}],
        }

        mock_client.post.side_effect = [token_resp, capture_resp]

        client = PayPalClient(client_id="test_id", secret="test_secret")
        res = client.capture_order("8AB99988877766655")
        self.assertEqual(res["status"], "COMPLETED")
        self.assertTrue(res.get("already_captured"))

    def test_confirm_payment_invalid_token_regex(self):
        """GET /api/confirm-payment with invalid token returns 400."""
        response = self.client.get("/api/confirm-payment?token=invalid!!!")
        self.assertEqual(response.status_code, 400)

    def test_confirm_payment_rejects_unregistered_demo_token(self):
        """Reject demo tokens unless they match an active demo transaction (Feature 5)."""
        response = self.client.get("/api/confirm-payment?token=DEMO-FAKE1234567")
        self.assertEqual(response.status_code, 400)
        self.assertIn("does not match active pending order", response.json()["detail"])

    def test_confirm_payment_rejects_unmatched_paypal_token(self):
        """Reject PayPal token that does not match the active pending order."""
        response = self.client.get("/api/confirm-payment?token=8AB12345678901234")
        self.assertEqual(response.status_code, 400)
        self.assertIn("does not match active pending order", response.json()["detail"])

    @patch("main.get_transaction")
    def test_confirm_payment_idempotent_when_already_captured(self, mock_get_tx):
        """Confirming payment for an already-captured transaction returns 200 idempotently."""
        mock_get_tx.return_value = {
            "session_id": "sess-1",
            "order_id": "DEMO-ALREADYCAPTURED",
            "status": "captured",
        }
        response = self.client.get("/api/confirm-payment?token=DEMO-ALREADYCAPTURED")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "captured")
        self.assertTrue(data.get("already_captured"))


if __name__ == "__main__":
    unittest.main()
