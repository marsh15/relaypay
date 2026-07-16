import pytest
from pydantic_core import ValidationError
from relaypay.contracts import PaymentIntentCreate


def test_payment_intent_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        PaymentIntentCreate.model_validate(
            {
                "customer_id": "cus_" + "a" * 32,
                "merchant_reference": "order-1",
                "amount": 1000,
                "currency": "INR",
                "unexpected": True,
            }
        )


def test_payment_intent_rejects_coerced_amount() -> None:
    with pytest.raises(ValidationError, match="valid integer"):
        PaymentIntentCreate.model_validate(
            {
                "customer_id": "cus_" + "a" * 32,
                "merchant_reference": "order-1",
                "amount": "1000",
                "currency": "INR",
            }
        )
