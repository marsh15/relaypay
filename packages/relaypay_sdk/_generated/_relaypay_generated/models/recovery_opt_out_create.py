from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, BinaryIO, TextIO, TYPE_CHECKING, Generator

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

from ..models.recovery_opt_out_create_channel import RecoveryOptOutCreateChannel
from ..types import UNSET, Unset






T = TypeVar("T", bound="RecoveryOptOutCreate")



@_attrs_define
class RecoveryOptOutCreate:
    """ 
        Attributes:
            source_event_id (str):
            channel (RecoveryOptOutCreateChannel | Unset):  Default: RecoveryOptOutCreateChannel.ALL.
     """

    source_event_id: str
    channel: RecoveryOptOutCreateChannel | Unset = RecoveryOptOutCreateChannel.ALL





    def to_dict(self) -> dict[str, Any]:
        source_event_id = self.source_event_id

        channel: str | Unset = UNSET
        if not isinstance(self.channel, Unset):
            channel = self.channel.value



        field_dict: dict[str, Any] = {}

        field_dict.update({
            "sourceEventId": source_event_id,
        })
        if channel is not UNSET:
            field_dict["channel"] = channel

        return field_dict



    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        source_event_id = d.pop("sourceEventId")

        _channel = d.pop("channel", UNSET)
        channel: RecoveryOptOutCreateChannel | Unset
        if isinstance(_channel,  Unset):
            channel = UNSET
        else:
            channel = RecoveryOptOutCreateChannel(_channel)




        recovery_opt_out_create = cls(
            source_event_id=source_event_id,
            channel=channel,
        )

        return recovery_opt_out_create

