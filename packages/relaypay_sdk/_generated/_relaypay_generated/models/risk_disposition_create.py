from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, BinaryIO, TextIO, TYPE_CHECKING, Generator

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

from ..models.risk_disposition_create_disposition import RiskDispositionCreateDisposition






T = TypeVar("T", bound="RiskDispositionCreate")



@_attrs_define
class RiskDispositionCreate:
    """ 
        Attributes:
            disposition (RiskDispositionCreateDisposition):
            note (str):
     """

    disposition: RiskDispositionCreateDisposition
    note: str





    def to_dict(self) -> dict[str, Any]:
        disposition = self.disposition.value

        note = self.note


        field_dict: dict[str, Any] = {}

        field_dict.update({
            "disposition": disposition,
            "note": note,
        })

        return field_dict



    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        disposition = RiskDispositionCreateDisposition(d.pop("disposition"))




        note = d.pop("note")

        risk_disposition_create = cls(
            disposition=disposition,
            note=note,
        )

        return risk_disposition_create

