from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, BinaryIO, TextIO, TYPE_CHECKING, Generator

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

from typing import cast






T = TypeVar("T", bound="Citation")



@_attrs_define
class Citation:
    """ 
        Attributes:
            field_paths (list[str]):
            record_id (str):
            record_type (str):
            snapshot_sha_256 (str):
     """

    field_paths: list[str]
    record_id: str
    record_type: str
    snapshot_sha_256: str





    def to_dict(self) -> dict[str, Any]:
        field_paths = self.field_paths



        record_id = self.record_id

        record_type = self.record_type

        snapshot_sha_256 = self.snapshot_sha_256


        field_dict: dict[str, Any] = {}

        field_dict.update({
            "fieldPaths": field_paths,
            "recordId": record_id,
            "recordType": record_type,
            "snapshotSha256": snapshot_sha_256,
        })

        return field_dict



    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        field_paths = cast(list[str], d.pop("fieldPaths"))


        record_id = d.pop("recordId")

        record_type = d.pop("recordType")

        snapshot_sha_256 = d.pop("snapshotSha256")

        citation = cls(
            field_paths=field_paths,
            record_id=record_id,
            record_type=record_type,
            snapshot_sha_256=snapshot_sha_256,
        )

        return citation

