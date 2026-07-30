from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, BinaryIO, TextIO, TYPE_CHECKING, Generator

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

from ..models.connector_verify_kind import ConnectorVerifyKind






T = TypeVar("T", bound="ConnectorVerify")



@_attrs_define
class ConnectorVerify:
    """ 
        Attributes:
            kind (ConnectorVerifyKind):
     """

    kind: ConnectorVerifyKind





    def to_dict(self) -> dict[str, Any]:
        kind = self.kind.value


        field_dict: dict[str, Any] = {}

        field_dict.update({
            "kind": kind,
        })

        return field_dict



    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        kind = ConnectorVerifyKind(d.pop("kind"))




        connector_verify = cls(
            kind=kind,
        )

        return connector_verify

