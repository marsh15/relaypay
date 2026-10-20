from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, BinaryIO, TextIO, TYPE_CHECKING, Generator

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

from ..types import UNSET, Unset
from typing import cast






T = TypeVar("T", bound="AuthorizeCommandV2")



@_attrs_define
class AuthorizeCommandV2:
    """ /api/v2 authorize: optionally hold funds for a custom period (default AUTH_HOLD_SECONDS).

        Attributes:
            hold_seconds (int | None | Unset):
     """

    hold_seconds: int | None | Unset = UNSET





    def to_dict(self) -> dict[str, Any]:
        hold_seconds: int | None | Unset
        if isinstance(self.hold_seconds, Unset):
            hold_seconds = UNSET
        else:
            hold_seconds = self.hold_seconds


        field_dict: dict[str, Any] = {}

        field_dict.update({
        })
        if hold_seconds is not UNSET:
            field_dict["hold_seconds"] = hold_seconds

        return field_dict



    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        def _parse_hold_seconds(data: object) -> int | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | None | Unset, data)

        hold_seconds = _parse_hold_seconds(d.pop("hold_seconds", UNSET))


        authorize_command_v2 = cls(
            hold_seconds=hold_seconds,
        )

        return authorize_command_v2

