from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, BinaryIO, TextIO, TYPE_CHECKING, Generator

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

from typing import Literal, cast






T = TypeVar("T", bound="PayoutCreate")



@_attrs_define
class PayoutCreate:
    """ 
        Attributes:
            amount (int):
            beneficiary_id (str):
            currency (Literal['INR']):
            merchant_account_id (str):
     """

    amount: int
    beneficiary_id: str
    currency: Literal['INR']
    merchant_account_id: str





    def to_dict(self) -> dict[str, Any]:
        amount = self.amount

        beneficiary_id = self.beneficiary_id

        currency = self.currency

        merchant_account_id = self.merchant_account_id


        field_dict: dict[str, Any] = {}

        field_dict.update({
            "amount": amount,
            "beneficiaryId": beneficiary_id,
            "currency": currency,
            "merchantAccountId": merchant_account_id,
        })

        return field_dict



    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        amount = d.pop("amount")

        beneficiary_id = d.pop("beneficiaryId")

        currency = cast(Literal['INR'] , d.pop("currency"))
        if currency != 'INR':
            raise ValueError(f"currency must match const 'INR', got '{currency}'")

        merchant_account_id = d.pop("merchantAccountId")

        payout_create = cls(
            amount=amount,
            beneficiary_id=beneficiary_id,
            currency=currency,
            merchant_account_id=merchant_account_id,
        )

        return payout_create

