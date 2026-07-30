from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, BinaryIO, TextIO, TYPE_CHECKING, Generator

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

from ..types import UNSET, Unset
from typing import cast






T = TypeVar("T", bound="MismatchResolution")



@_attrs_define
class MismatchResolution:
    """ 
        Attributes:
            note (str):
            compensating_journal_id (None | str | Unset):
     """

    note: str
    compensating_journal_id: None | str | Unset = UNSET





    def to_dict(self) -> dict[str, Any]:
        note = self.note

        compensating_journal_id: None | str | Unset
        if isinstance(self.compensating_journal_id, Unset):
            compensating_journal_id = UNSET
        else:
            compensating_journal_id = self.compensating_journal_id


        field_dict: dict[str, Any] = {}

        field_dict.update({
            "note": note,
        })
        if compensating_journal_id is not UNSET:
            field_dict["compensatingJournalId"] = compensating_journal_id

        return field_dict



    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        note = d.pop("note")

        def _parse_compensating_journal_id(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        compensating_journal_id = _parse_compensating_journal_id(d.pop("compensatingJournalId", UNSET))


        mismatch_resolution = cls(
            note=note,
            compensating_journal_id=compensating_journal_id,
        )

        return mismatch_resolution

