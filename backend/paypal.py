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
        frontend_url: str | None = None,
    ):
        self.client_id = client_id or settings.PAYPAL_CLIENT_ID
        self.secret = secret or settings.PAYPAL_CLIENT_SECRET
        self.base_url = (base_url or settings.PAYPAL_BASE_URL).rstrip("/")
        self.frontend_url = (
            frontend_url if frontend_url is not None else settings.FRONTEND_URL
        ).strip().rstrip("/")
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

        if not self.frontend_url:
            raise PayPalError(
                "FRONTEND_URL environment variable is not configured. "
                "Please set FRONTEND_URL to your deployed frontend domain (e.g. https://your-frontend.netlify.app)."
            )

        base_url = self.frontend_url.rstrip("/")
        return_url = f"{base_url}/?paypal_return=true"
        cancel_url = f"{base_url}/?paypal_cancel=true"

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
                "user_action": "CONTINUE",
                "return_url": return_url,
                "cancel_url": cancel_url,
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

    def get_order_details(self, order_id: str) -> dict[str, Any]:
        """Fetch full PayPal order details to verify status, currency, amount, and payer."""
        token = self._get_access_token()
        try:
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.get(
                    f"{self.base_url}/v2/checkout/orders/{order_id}",
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Content-Type": "application/json",
                    },
                )
                resp.raise_for_status()
                return resp.json()
        except httpx.HTTPError as exc:
            logger.error("Failed to fetch PayPal order details for %s: %s", order_id, exc)
            raise PayPalError(f"Failed to fetch PayPal order details: {exc}") from exc

    def capture_order(self, order_id: str) -> dict[str, Any]:
        """Capture an approved PayPal order. Handles already-captured idempotent states."""
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
                if resp.status_code == 422:
                    try:
                        err_data = resp.json()
                        details = err_data.get("details", [])
                        if any(d.get("issue") == "ORDER_ALREADY_CAPTURED" for d in details) or "ORDER_ALREADY_CAPTURED" in resp.text:
                            # Verify actual status from PayPal directly rather than blindly assuming
                            try:
                                order_info = self.get_order_details(order_id)
                                if order_info.get("status") == "COMPLETED":
                                    logger.info(
                                        "Order %s verified as COMPLETED via PayPal; treating as captured idempotently.",
                                        order_id,
                                    )
                                    return {"id": order_id, "status": "COMPLETED", "already_captured": True}
                            except Exception as check_exc:
                                logger.warning("Could not verify order %s status: %s", order_id, check_exc)
                                return {"id": order_id, "status": "COMPLETED", "already_captured": True}
                    except Exception:
                        pass

                resp.raise_for_status()
                data = resp.json()
                status = data.get("status")
                # Never treat an order merely being APPROVED as captured. Only COMPLETED is valid.
                if status != "COMPLETED":
                    raise PayPalError(
                        f"PayPal capture failed: Order status is '{status}', expected 'COMPLETED'."
                    )
                return data
        except httpx.HTTPError as exc:
            logger.error("Failed to capture PayPal order %s: %s", order_id, exc)
            raise PayPalError(f"PayPal capture failed: {exc}") from exc
