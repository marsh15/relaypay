from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, BinaryIO, TextIO, TYPE_CHECKING, Generator

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset







T = TypeVar("T", bound="RiskAnnotationCreate")



@_attrs_define
class RiskAnnotationCreate:
    """ 
        Attributes:
            note (str):
     """

    note: str





    def to_dict(self) -> dict[str, Any]:
        note = self.note


        field_dict: dict[str, Any] = {}

        field_dict.update({
            "note": note,
        })

        return field_dict



    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        note = d.pop("note")

        risk_annotation_create = cls(
            note=note,
        )

        return risk_annotation_create

