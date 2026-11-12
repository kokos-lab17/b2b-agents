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
        self.assertEqual(response.json().get("status"), "started")
        self.assertIn("session_id", response.json())

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

        # Mock GET order details returning verified COMPLETED status
        get_order_resp = MagicMock()
        get_order_resp.status_code = 200
        get_order_resp.json.return_value = {
            "id": "8AB99988877766655",
            "status": "COMPLETED",
        }
        mock_client.get.return_value = get_order_resp

        client = PayPalClient(client_id="test_id", secret="test_secret")
        res = client.capture_order("8AB99988877766655")
        self.assertEqual(res["status"], "COMPLETED")
        self.assertTrue(res.get("already_captured"))

    @patch("backend.paypal.httpx.Client")
    def test_paypal_capture_order_fails_closed_if_verification_fails(self, mock_client_cls):
        """If PayPal returns 422 ORDER_ALREADY_CAPTURED but verification fails, must fail closed (Requirement 11.4)."""
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_client

        token_resp = MagicMock()
        token_resp.json.return_value = {"access_token": "mock_token_123"}
        token_resp.status_code = 200

        capture_resp = MagicMock()
        capture_resp.status_code = 422
        capture_resp.text = '{"name":"UNPROCESSABLE_ENTITY","details":[{"issue":"ORDER_ALREADY_CAPTURED"}]}'
        capture_resp.json.return_value = {
            "name": "UNPROCESSABLE_ENTITY",
            "details": [{"issue": "ORDER_ALREADY_CAPTURED"}],
        }

        mock_client.post.side_effect = [token_resp, capture_resp]
        mock_client.get.side_effect = Exception("Network timeout during verification")

        client = PayPalClient(client_id="test_id", secret="test_secret")
        with self.assertRaises(PayPalError) as ctx:
            client.capture_order("8AB99988877766655")
        self.assertIn("Failed to independently verify already-captured status", str(ctx.exception))

    def test_confirm_payment_invalid_token_regex(self):
        """GET /api/confirm-payment with invalid token returns 400."""
        response = self.client.get("/api/confirm-payment?token=invalid!!!")
        self.assertEqual(response.status_code, 400)

    def test_confirm_payment_rejects_unregistered_demo_token(self):
        """Reject demo tokens unless they match an active demo transaction (Feature 5)."""
        response = self.client.get("/api/confirm-payment?token=DEMO-FAKE1234567")
        self.assertEqual(response.status_code, 400)
        self.assertIn("does not match active pending order", response.json()["detail"])

    @patch("main.get_transaction", return_value=None)
    def test_confirm_payment_rejects_unmatched_paypal_token(self, mock_get_tx):
        """Reject PayPal token that does not match the active pending order."""
        response = self.client.get("/api/confirm-payment?token=8AB99999999999999")
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

    @patch("main.PayPalClient")
    @patch("main.get_transaction")
    def test_confirm_payment_rejects_unapproved_order(self, mock_get_tx, mock_pp_cls):
        """Cannot capture PayPal order before buyer approval on PayPal (status must be APPROVED)."""
        mock_get_tx.return_value = {
            "session_id": "sess-1",
            "order_id": "8AB12345678901234",
            "amount": 5000.0,
            "status": "awaiting_approval",
        }
        mock_pp = MagicMock()
        mock_pp_cls.return_value = mock_pp
        # Order is still in CREATED status (not approved by buyer yet)
        mock_pp.get_order_details.return_value = {
            "id": "8AB12345678901234",
            "status": "CREATED",
            "purchase_units": [{"amount": {"currency_code": "USD", "value": "5000.00"}}],
        }

        response = self.client.get("/api/confirm-payment?token=8AB12345678901234")
        self.assertEqual(response.status_code, 400)
        self.assertIn("Buyer has not approved this order", response.json()["detail"])
        # Ensure capture was NEVER called
        mock_pp.capture_order.assert_not_called()

    @patch("main.PayPalClient")
    @patch("main.get_transaction")
    def test_confirm_payment_validates_currency_and_amount(self, mock_get_tx, mock_pp_cls):
        """Reject capture if PayPal order amount or currency does not match the agreed deal."""
        mock_get_tx.return_value = {
            "session_id": "sess-1",
            "order_id": "8AB12345678901234",
            "amount": 5000.0,
            "status": "awaiting_approval",
        }
        mock_pp = MagicMock()
        mock_pp_cls.return_value = mock_pp
        # Currency mismatch test
        mock_pp.get_order_details.return_value = {
            "id": "8AB12345678901234",
            "status": "APPROVED",
            "purchase_units": [{"amount": {"currency_code": "EUR", "value": "5000.00"}}],
        }
        resp1 = self.client.get("/api/confirm-payment?token=8AB12345678901234")
        self.assertEqual(resp1.status_code, 400)
        self.assertIn("Currency mismatch", resp1.json()["detail"])

        # Amount mismatch test
        mock_pp.get_order_details.return_value = {
            "id": "8AB12345678901234",
            "status": "APPROVED",
            "purchase_units": [{"amount": {"currency_code": "USD", "value": "2500.00"}}],
        }
        resp2 = self.client.get("/api/confirm-payment?token=8AB12345678901234")
        self.assertEqual(resp2.status_code, 400)
        self.assertIn("Amount mismatch", resp2.json()["detail"])

    @patch("main.PayPalClient")
    @patch("main.get_transaction")
    @patch("main.record_payment_captured")
    def test_confirm_payment_captures_valid_approved_order(self, mock_record, mock_get_tx, mock_pp_cls):
        """Approved order with matching terms captures successfully and records in memory."""
        mock_get_tx.return_value = {
            "session_id": "sess-1",
            "order_id": "8AB12345678901234",
            "amount": 5000.0,
            "status": "awaiting_approval",
        }
        mock_record.return_value = True
        mock_pp = MagicMock()
        mock_pp_cls.return_value = mock_pp
        mock_pp.get_order_details.return_value = {
            "id": "8AB12345678901234",
            "status": "APPROVED",
            "purchase_units": [{"amount": {"currency_code": "USD", "value": "5000.00"}}],
        }
        mock_pp.capture_order.return_value = {
            "id": "8AB12345678901234",
            "status": "COMPLETED",
        }

        response = self.client.get("/api/confirm-payment?token=8AB12345678901234")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "captured")
        mock_pp.capture_order.assert_called_once_with("8AB12345678901234")
        mock_record.assert_called_once_with("8AB12345678901234")

    @patch("backend.paypal.httpx.Client")
    def test_paypal_client_capture_order_fails_if_status_not_completed(self, mock_client_cls):
        """Never treat an order merely being APPROVED as captured. Only COMPLETED is valid."""
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_client

        token_resp = MagicMock()
        token_resp.json.return_value = {"access_token": "mock_token_123"}
        token_resp.status_code = 200

        # Simulate PayPal capture response returning APPROVED instead of COMPLETED
        capture_resp = MagicMock()
        capture_resp.status_code = 200
        capture_resp.json.return_value = {
            "id": "8AB12345678901234",
            "status": "APPROVED",
        }

        mock_client.post.side_effect = [token_resp, capture_resp]
        client = PayPalClient(client_id="test_id", secret="test_secret")
        with self.assertRaises(PayPalError) as ctx:
            client.capture_order("8AB12345678901234")
        self.assertIn("expected 'COMPLETED'", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
