"""Data models and schemas for multi-agent B2B procurement."""
from typing import Any
from pydantic import BaseModel, Field


class CommercialOffer(BaseModel):
    """Structured multi-variable commercial offer (Feature 3)."""
    unit_price: float = Field(..., gt=0, description="Price per unit in USD")
    total_price: float = Field(..., gt=0, description="Total procurement cost in USD")
    quantity: int = Field(..., gt=0, description="Order quantity")
    delivery_days: int = Field(..., ge=1, description="Lead time in calendar days")
    warranty_months: int = Field(..., ge=0, description="Warranty duration in months")
    shipping_cost: float = Field(0.0, ge=0, description="Shipping cost in USD (0 = Free)")
    payment_terms: str = Field(
        "Net 30",
        description="Payment terms (e.g. Net 30, Net 60, Due on Delivery, Prepaid)"
    )
    special_concessions: list[str] = Field(
        default_factory=list,
        description="Non-price concessions such as dedicated support, inspections, volume discounts"
    )

    def calculate_total(self) -> float:
        return round(self.unit_price * self.quantity + self.shipping_cost, 2)


class PrivateSellerConstraint(BaseModel):
    """Dynamically generated confidential reservation constraints (Feature 1).
    NEVER exposed to buyer prompts, API responses, or frontend feeds.
    """
    min_unit_price: float = Field(..., gt=0, description="Confidential seller floor price")
    min_delivery_days: int = Field(..., ge=1, description="Absolute fastest delivery time")
    max_warranty_months: int = Field(..., ge=0, description="Maximum warranty seller can offer")
    min_shipping_cost: float = Field(0.0, ge=0, description="Minimum freight cost acceptable")
    allowed_payment_terms: list[str] = Field(
        default_factory=lambda: ["Net 30", "Net 60", "Due on Delivery", "Prepaid"]
    )
    target_unit_price: float = Field(..., gt=0, description="Ideal target price for seller")


class CommercialScoreBreakdown(BaseModel):
    """Deterministic commercial evaluation breakdown (Feature 4)."""
    total_score: float = Field(..., ge=0, le=100)
    price_score: float = Field(..., ge=0, le=40)
    delivery_score: float = Field(..., ge=0, le=15)
    warranty_score: float = Field(..., ge=0, le=15)
    shipping_score: float = Field(..., ge=0, le=10)
    terms_score: float = Field(..., ge=0, le=10)
    reliability_score: float = Field(..., ge=0, le=10)
    explanation: str = Field(..., description="Explainable commercial reasoning")


class Event(BaseModel):
    """Explainable structured event for the activity feed (Feature 8)."""
    step: int
    timestamp: str
    agent: str
    event_type: str
    message: str
    strategy: str | None = None
    offer: float | None = None
    details: dict[str, Any] | None = None
    status: str
    approve_url: str | None = None


class SupplierProfile(BaseModel):
    """Public supplier profile (Feature 1 & Feature 5)."""
    name: str
    archetype: str
    style: str
    specialty: str
    attempted_deals: int = 0
    accepted_deals: int = 0
    captured_payments: int = 0
    reliability_score: float = 1.0


class TokenUsageMetadata(BaseModel):
    """Provider usage metadata (Feature 9)."""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    total_calls: int = 0
    is_demo: bool = False


class StartRequest(BaseModel):
    """Request payload to initiate procurement."""
    product: str = Field(..., min_length=1)
    qty: int = Field(..., gt=0)
    budget: float = Field(..., gt=0)
    demo_mode: bool = False


class StatusResponse(BaseModel):
    """Status endpoint payload preserving existing schema while extending it."""
    events: list[dict[str, Any]]
    order_id: str | None = None
    approve_url: str | None = None
    status: str
    vendors: list[dict[str, Any]] = Field(default_factory=list)
    deal: dict[str, Any] | None = None
    token_usage: dict[str, Any] | None = None
