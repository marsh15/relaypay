from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, BinaryIO, TextIO, TYPE_CHECKING, Generator

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

from ..models.subscription_consent_channels_item import SubscriptionConsentChannelsItem
from typing import cast






T = TypeVar("T", bound="SubscriptionConsent")



@_attrs_define
class SubscriptionConsent:
    """ 
        Attributes:
            channels (list[SubscriptionConsentChannelsItem]):
            display_name (str):
     """

    channels: list[SubscriptionConsentChannelsItem]
    display_name: str





    def to_dict(self) -> dict[str, Any]:
        channels = []
        for channels_item_data in self.channels:
            channels_item = channels_item_data.value
            channels.append(channels_item)



        display_name = self.display_name


        field_dict: dict[str, Any] = {}

        field_dict.update({
            "channels": channels,
            "displayName": display_name,
        })

        return field_dict



    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        channels = []
        _channels = d.pop("channels")
        for channels_item_data in (_channels):
            channels_item = SubscriptionConsentChannelsItem(channels_item_data)



            channels.append(channels_item)


        display_name = d.pop("displayName")

        subscription_consent = cls(
            channels=channels,
            display_name=display_name,
        )

        return subscription_consent

