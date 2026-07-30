from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, BinaryIO, TextIO, TYPE_CHECKING, Generator

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

from ..types import UNSET, Unset
from typing import Literal, cast






T = TypeVar("T", bound="PaymentIntentCreate")



@_attrs_define
class PaymentIntentCreate:
    """ 
        Attributes:
            amount (int):
            customer_id (str):
            merchant_reference (str):
            currency (Literal['INR'] | Unset):  Default: 'INR'.
     """

    amount: int
    customer_id: str
    merchant_reference: str
    currency: Literal['INR'] | Unset = 'INR'





    def to_dict(self) -> dict[str, Any]:
        amount = self.amount

        customer_id = self.customer_id

        merchant_reference = self.merchant_reference

        currency = self.currency


        field_dict: dict[str, Any] = {}

        field_dict.update({
            "amount": amount,
            "customer_id": customer_id,
            "merchant_reference": merchant_reference,
        })
        if currency is not UNSET:
            field_dict["currency"] = currency

        return field_dict



    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        amount = d.pop("amount")

        customer_id = d.pop("customer_id")

        merchant_reference = d.pop("merchant_reference")

        currency = cast(Literal['INR'] | Unset , d.pop("currency", UNSET))
        if currency != 'INR'and not isinstance(currency, Unset):
            raise ValueError(f"currency must match const 'INR', got '{currency}'")

        payment_intent_create = cls(
            amount=amount,
            customer_id=customer_id,
            merchant_reference=merchant_reference,
            currency=currency,
        )

        return payment_intent_create

