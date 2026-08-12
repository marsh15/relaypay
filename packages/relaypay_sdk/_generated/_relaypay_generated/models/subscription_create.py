from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, BinaryIO, TextIO, TYPE_CHECKING, Generator

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

from typing import cast

if TYPE_CHECKING:
  from ..models.subscription_consent import SubscriptionConsent





T = TypeVar("T", bound="SubscriptionCreate")



@_attrs_define
class SubscriptionCreate:
    """ 
        Attributes:
            amount (int):
            consent (SubscriptionConsent):
            customer_id (str):
            external_id (str):
            plan_reference (str):
     """

    amount: int
    consent: SubscriptionConsent
    customer_id: str
    external_id: str
    plan_reference: str





    def to_dict(self) -> dict[str, Any]:
        from ..models.subscription_consent import SubscriptionConsent
        amount = self.amount

        consent = self.consent.to_dict()

        customer_id = self.customer_id

        external_id = self.external_id

        plan_reference = self.plan_reference


        field_dict: dict[str, Any] = {}

        field_dict.update({
            "amount": amount,
            "consent": consent,
            "customerId": customer_id,
            "externalId": external_id,
            "planReference": plan_reference,
        })

        return field_dict



    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.subscription_consent import SubscriptionConsent
        d = dict(src_dict)
        amount = d.pop("amount")

        consent = SubscriptionConsent.from_dict(d.pop("consent"))




        customer_id = d.pop("customerId")

        external_id = d.pop("externalId")

        plan_reference = d.pop("planReference")

        subscription_create = cls(
            amount=amount,
            consent=consent,
            customer_id=customer_id,
            external_id=external_id,
            plan_reference=plan_reference,
        )

        return subscription_create

