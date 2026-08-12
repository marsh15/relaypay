from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, BinaryIO, TextIO, TYPE_CHECKING, Generator

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

from typing import cast
import datetime






T = TypeVar("T", bound="SubscriptionInvoiceCreate")



@_attrs_define
class SubscriptionInvoiceCreate:
    """ 
        Attributes:
            due_at (datetime.datetime):
            external_id (str):
     """

    due_at: datetime.datetime
    external_id: str





    def to_dict(self) -> dict[str, Any]:
        due_at = self.due_at.isoformat()

        external_id = self.external_id


        field_dict: dict[str, Any] = {}

        field_dict.update({
            "dueAt": due_at,
            "externalId": external_id,
        })

        return field_dict



    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        due_at = datetime.datetime.fromisoformat(d.pop("dueAt"))




        external_id = d.pop("externalId")

        subscription_invoice_create = cls(
            due_at=due_at,
            external_id=external_id,
        )

        return subscription_invoice_create

