"""Server-side PayPal Sandbox REST API client (Feature 6)."""
import logging
from typing import Any
import httpx
from backend.config import settings
from backend.models import CommercialOffer

logger = logging.getLogger(__name__)


class PayPalError(Exception):
    """Raised when PayPal Sandbox API encounters an error."""


class PayPalClient:
    """Manages order creation and capture directly via PayPal Sandbox REST API."""

    def __init__(
        self,
        client_id: str | None = None,
        secret: str | None = None,
        base_url: str | None = None,
    ):
        self.client_id = client_id or settings.PAYPAL_CLIENT_ID
        self.secret = secret or settings.PAYPAL_CLIENT_SECRET
        self.base_url = (base_url or settings.PAYPAL_BASE_URL).rstrip("/")
        self.timeout = settings.REQUEST_TIMEOUT_SECONDS

    def is_configured(self) -> bool:
        return bool(self.client_id and self.secret)

    def _get_access_token(self) -> str:
        """Fetch OAuth2 Bearer token using client credentials."""
        if not self.is_configured():
            raise PayPalError(
                "PayPal Sandbox credentials not configured. Please set PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET."
            )

        try:
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.post(
                    f"{self.base_url}/v1/oauth2/token",
                    data={"grant_type": "client_credentials"},
                    auth=(self.client_id, self.secret),
                    headers={"Accept": "application/json"},
                )
                resp.raise_for_status()
                data = resp.json()
                token = data.get("access_token")
                if not token:
                    raise PayPalError("PayPal response missing access_token.")
                return token
        except httpx.HTTPError as exc:
            logger.error("Failed to authenticate with PayPal Sandbox: %s", exc)
            raise PayPalError(f"PayPal Sandbox authentication failed: {exc}") from exc

    def create_order(
        self, product: str, qty: int, offer: CommercialOffer
    ) -> tuple[str, str]:
        """Create a PayPal Sandbox order with intent CAPTURE.
        Returns: (order_id, approve_url)
        """
        # Backend financial validation before creating order
        if offer.unit_price <= 0 or qty <= 0:
            raise PayPalError(
                f"Cannot create order: Invalid terms (unit_price={offer.unit_price}, qty={qty})"
            )

        token = self._get_access_token()
        total_amount = round(offer.unit_price * qty + offer.shipping_cost, 2)
        item_total = round(offer.unit_price * qty, 2)
        shipping_val = round(offer.shipping_cost, 2)

        payload = {
            "intent": "CAPTURE",
            "purchase_units": [
                {
                    "reference_id": "default",
                    "description": f"B2B Order: {qty}x {product}",
                    "amount": {
                        "currency_code": "USD",
                        "value": f"{total_amount:.2f}",
                        "breakdown": {
                            "item_total": {
                                "currency_code": "USD",
                                "value": f"{item_total:.2f}",
                            },
                            "shipping": {
                                "currency_code": "USD",
                                "value": f"{shipping_val:.2f}",
                            },
                        },
                    },
                    "items": [
                        {
                            "name": product[:127],
                            "unit_amount": {
                                "currency_code": "USD",
                                "value": f"{offer.unit_price:.2f}",
                            },
                            "quantity": str(qty),
                            "category": "PHYSICAL_GOODS",
                        }
                    ],
                }
            ],
            "application_context": {
                "brand_name": "Autonomous B2B Procurement",
                "landing_page": "BILLING",
                "user_action": "PAY_NOW",
                "return_url": "https://example.com/return",
                "cancel_url": "https://example.com/cancel",
            },
        }

        try:
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.post(
                    f"{self.base_url}/v2/checkout/orders",
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
                resp.raise_for_status()
                data = resp.json()

                order_id = data.get("id")
                if not order_id:
                    raise PayPalError(f"PayPal response missing order id: {data}")

                approve_url = None
                for link in data.get("links", []):
                    if link.get("rel") == "approve":
                        approve_url = link.get("href")
                        break

                if not approve_url:
                    raise PayPalError(
                        f"PayPal order {order_id} did not return an approval URL: {data}"
                    )

                logger.info("Created PayPal Sandbox order %s with approve URL", order_id)
                return order_id, approve_url

        except httpx.HTTPError as exc:
            logger.error("Failed to create PayPal Sandbox order: %s", exc)
            raise PayPalError(f"PayPal order creation failed: {exc}") from exc

    def capture_order(self, order_id: str) -> dict[str, Any]:
        """Capture an approved PayPal order."""
        token = self._get_access_token()
        try:
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.post(
                    f"{self.base_url}/v2/checkout/orders/{order_id}/capture",
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Content-Type": "application/json",
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                status = data.get("status")
                if status != "COMPLETED":
                    raise PayPalError(
                        f"PayPal capture failed: Order status is {status}, expected COMPLETED."
                    )
                return data
        except httpx.HTTPError as exc:
            logger.error("Failed to capture PayPal order %s: %s", order_id, exc)
            raise PayPalError(f"PayPal capture failed: {exc}") from exc
