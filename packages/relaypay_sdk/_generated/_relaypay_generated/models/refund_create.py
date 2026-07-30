from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, BinaryIO, TextIO, TYPE_CHECKING, Generator

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

from ..types import UNSET, Unset
from typing import cast
from typing import Literal, cast






T = TypeVar("T", bound="RefundCreate")



@_attrs_define
class RefundCreate:
    """ 
        Attributes:
            amount (int):
            currency (Literal['INR'] | Unset):  Default: 'INR'.
            merchant_refund_reference (None | str | Unset):
     """

    amount: int
    currency: Literal['INR'] | Unset = 'INR'
    merchant_refund_reference: None | str | Unset = UNSET





    def to_dict(self) -> dict[str, Any]:
        amount = self.amount

        currency = self.currency

        merchant_refund_reference: None | str | Unset
        if isinstance(self.merchant_refund_reference, Unset):
            merchant_refund_reference = UNSET
        else:
            merchant_refund_reference = self.merchant_refund_reference


        field_dict: dict[str, Any] = {}

        field_dict.update({
            "amount": amount,
        })
        if currency is not UNSET:
            field_dict["currency"] = currency
        if merchant_refund_reference is not UNSET:
            field_dict["merchant_refund_reference"] = merchant_refund_reference

        return field_dict



    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        amount = d.pop("amount")

        currency = cast(Literal['INR'] | Unset , d.pop("currency", UNSET))
        if currency != 'INR'and not isinstance(currency, Unset):
            raise ValueError(f"currency must match const 'INR', got '{currency}'")

        def _parse_merchant_refund_reference(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        merchant_refund_reference = _parse_merchant_refund_reference(d.pop("merchant_refund_reference", UNSET))


        refund_create = cls(
            amount=amount,
            currency=currency,
            merchant_refund_reference=merchant_refund_reference,
        )

        return refund_create

