from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, BinaryIO, TextIO, TYPE_CHECKING, Generator

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

from ..models.settlement_policy_create_weekendhandling import SettlementPolicyCreateWeekendhandling






T = TypeVar("T", bound="SettlementPolicyCreate")



@_attrs_define
class SettlementPolicyCreate:
    """ 
        Attributes:
            cutoff_hour (int):
            cutoff_minute (int):
            merchant_account_id (str):
            settlement_delay_days (int):
            timezone (str):
            weekend_handling (SettlementPolicyCreateWeekendhandling):
     """

    cutoff_hour: int
    cutoff_minute: int
    merchant_account_id: str
    settlement_delay_days: int
    timezone: str
    weekend_handling: SettlementPolicyCreateWeekendhandling





    def to_dict(self) -> dict[str, Any]:
        cutoff_hour = self.cutoff_hour

        cutoff_minute = self.cutoff_minute

        merchant_account_id = self.merchant_account_id

        settlement_delay_days = self.settlement_delay_days

        timezone = self.timezone

        weekend_handling = self.weekend_handling.value


        field_dict: dict[str, Any] = {}

        field_dict.update({
            "cutoffHour": cutoff_hour,
            "cutoffMinute": cutoff_minute,
            "merchantAccountId": merchant_account_id,
            "settlementDelayDays": settlement_delay_days,
            "timezone": timezone,
            "weekendHandling": weekend_handling,
        })

        return field_dict



    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        cutoff_hour = d.pop("cutoffHour")

        cutoff_minute = d.pop("cutoffMinute")

        merchant_account_id = d.pop("merchantAccountId")

        settlement_delay_days = d.pop("settlementDelayDays")

        timezone = d.pop("timezone")

        weekend_handling = SettlementPolicyCreateWeekendhandling(d.pop("weekendHandling"))




        settlement_policy_create = cls(
            cutoff_hour=cutoff_hour,
            cutoff_minute=cutoff_minute,
            merchant_account_id=merchant_account_id,
            settlement_delay_days=settlement_delay_days,
            timezone=timezone,
            weekend_handling=weekend_handling,
        )

        return settlement_policy_create

