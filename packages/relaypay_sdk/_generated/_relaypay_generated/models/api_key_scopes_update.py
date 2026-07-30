from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, BinaryIO, TextIO, TYPE_CHECKING, Generator

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

from typing import cast






T = TypeVar("T", bound="APIKeyScopesUpdate")



@_attrs_define
class APIKeyScopesUpdate:
    """ 
        Attributes:
            scopes (list[str]):
     """

    scopes: list[str]





    def to_dict(self) -> dict[str, Any]:
        scopes = self.scopes




        field_dict: dict[str, Any] = {}

        field_dict.update({
            "scopes": scopes,
        })

        return field_dict



    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        scopes = cast(list[str], d.pop("scopes"))


        api_key_scopes_update = cls(
            scopes=scopes,
        )

        return api_key_scopes_update

