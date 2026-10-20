from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, BinaryIO, TextIO, TYPE_CHECKING, Generator

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

from ..types import UNSET, Unset
from typing import cast






T = TypeVar("T", bound="CaptureCommandV2")



@_attrs_define
class CaptureCommandV2:
    """ /api/v2 capture: optionally capture less than the authorization (the rest is released).

        Attributes:
            amount (int | None | Unset):
     """

    amount: int | None | Unset = UNSET





    def to_dict(self) -> dict[str, Any]:
        amount: int | None | Unset
        if isinstance(self.amount, Unset):
            amount = UNSET
        else:
            amount = self.amount


        field_dict: dict[str, Any] = {}

        field_dict.update({
        })
        if amount is not UNSET:
            field_dict["amount"] = amount

        return field_dict



    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        def _parse_amount(data: object) -> int | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | None | Unset, data)

        amount = _parse_amount(d.pop("amount", UNSET))


        capture_command_v2 = cls(
            amount=amount,
        )

        return capture_command_v2

