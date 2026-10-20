from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

PositivePaise = Annotated[int, Field(strict=True, gt=0, le=9_223_372_036_854_775_807)]
Currency = Literal["INR"]


class StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class APIErrorBody(BaseModel):
    code: str
    message: str
    details: dict[str, Any] | None = None


class APIErrorEnvelope(BaseModel):
    error: APIErrorBody


class CustomerCreate(StrictRequest):
    merchant_customer_reference: Annotated[str, Field(min_length=1, max_length=128)]
    display_name: Annotated[str, Field(min_length=1, max_length=128)] | None = None


class PaymentIntentCreate(StrictRequest):
    customer_id: Annotated[str, Field(pattern=r"^cus_[0-9a-f]{32}$")]
    merchant_reference: Annotated[str, Field(min_length=1, max_length=128)]
    amount: PositivePaise
    currency: Currency = "INR"


class EmptyCommand(StrictRequest):
    pass


class AuthorizeCommandV2(StrictRequest):
    """/api/v2 authorize: optionally hold funds for a custom period (default AUTH_HOLD_SECONDS)."""

    hold_seconds: Annotated[int, Field(strict=True, ge=60, le=2_592_000)] | None = None


class CaptureCommandV2(StrictRequest):
    """/api/v2 capture: optionally capture less than the authorization (the rest is released)."""

    amount: PositivePaise | None = None


class RefundCreate(StrictRequest):
    amount: PositivePaise
    currency: Currency = "INR"
    merchant_refund_reference: Annotated[str, Field(min_length=1, max_length=128)] | None = None


class PaymentIntentSummary(BaseModel):
    id: str
    merchantReference: str
    amount: int
    currency: Currency
    authorizationStatus: str | None
    captureStatus: str | None
    createdAt: str


class PaymentIntentPage(BaseModel):
    data: list[PaymentIntentSummary]
    nextCursor: str | None
