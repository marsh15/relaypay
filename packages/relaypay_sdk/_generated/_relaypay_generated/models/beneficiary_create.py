from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, BinaryIO, TextIO, TYPE_CHECKING, Generator

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset







T = TypeVar("T", bound="BeneficiaryCreate")



@_attrs_define
class BeneficiaryCreate:
    """ 
        Attributes:
            bank_account_reference (str):
            display_name (str):
            reference (str):
     """

    bank_account_reference: str
    display_name: str
    reference: str





    def to_dict(self) -> dict[str, Any]:
        bank_account_reference = self.bank_account_reference

        display_name = self.display_name

        reference = self.reference


        field_dict: dict[str, Any] = {}

        field_dict.update({
            "bankAccountReference": bank_account_reference,
            "displayName": display_name,
            "reference": reference,
        })

        return field_dict



    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        bank_account_reference = d.pop("bankAccountReference")

        display_name = d.pop("displayName")

        reference = d.pop("reference")

        beneficiary_create = cls(
            bank_account_reference=bank_account_reference,
            display_name=display_name,
            reference=reference,
        )

        return beneficiary_create

