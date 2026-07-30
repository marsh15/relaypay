from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, BinaryIO, TextIO, TYPE_CHECKING, Generator

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

from typing import cast
from typing import Literal, cast






T = TypeVar("T", bound="PaymentIntentSummary")



@_attrs_define
class PaymentIntentSummary:
    """ 
        Attributes:
            amount (int):
            authorization_status (None | str):
            capture_status (None | str):
            created_at (str):
            currency (Literal['INR']):
            id (str):
            merchant_reference (str):
     """

    amount: int
    authorization_status: None | str
    capture_status: None | str
    created_at: str
    currency: Literal['INR']
    id: str
    merchant_reference: str
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)





    def to_dict(self) -> dict[str, Any]:
        amount = self.amount

        authorization_status: None | str
        authorization_status = self.authorization_status

        capture_status: None | str
        capture_status = self.capture_status

        created_at = self.created_at

        currency = self.currency

        id = self.id

        merchant_reference = self.merchant_reference


        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({
            "amount": amount,
            "authorizationStatus": authorization_status,
            "captureStatus": capture_status,
            "createdAt": created_at,
            "currency": currency,
            "id": id,
            "merchantReference": merchant_reference,
        })

        return field_dict



    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        amount = d.pop("amount")

        def _parse_authorization_status(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        authorization_status = _parse_authorization_status(d.pop("authorizationStatus"))


        def _parse_capture_status(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        capture_status = _parse_capture_status(d.pop("captureStatus"))


        created_at = d.pop("createdAt")

        currency = cast(Literal['INR'] , d.pop("currency"))
        if currency != 'INR':
            raise ValueError(f"currency must match const 'INR', got '{currency}'")

        id = d.pop("id")

        merchant_reference = d.pop("merchantReference")

        payment_intent_summary = cls(
            amount=amount,
            authorization_status=authorization_status,
            capture_status=capture_status,
            created_at=created_at,
            currency=currency,
            id=id,
            merchant_reference=merchant_reference,
        )


        payment_intent_summary.additional_properties = d
        return payment_intent_summary

    @property
    def additional_keys(self) -> list[str]:
        return list(self.additional_properties.keys())

    def __getitem__(self, key: str) -> Any:
        return self.additional_properties[key]

    def __setitem__(self, key: str, value: Any) -> None:
        self.additional_properties[key] = value

    def __delitem__(self, key: str) -> None:
        del self.additional_properties[key]

    def __contains__(self, key: str) -> bool:
        return key in self.additional_properties
