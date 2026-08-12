from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, BinaryIO, TextIO, TYPE_CHECKING, Generator

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

from ..models.recurring_failure_create_outcome import RecurringFailureCreateOutcome
from typing import cast

if TYPE_CHECKING:
  from ..models.evidence import Evidence





T = TypeVar("T", bound="RecurringFailureCreate")



@_attrs_define
class RecurringFailureCreate:
    """ 
        Attributes:
            evidence (Evidence):
            outcome (RecurringFailureCreateOutcome):
            provider_attempt_id (str):
            provider_code (str):
            subscription_id (str):
     """

    evidence: Evidence
    outcome: RecurringFailureCreateOutcome
    provider_attempt_id: str
    provider_code: str
    subscription_id: str





    def to_dict(self) -> dict[str, Any]:
        from ..models.evidence import Evidence
        evidence = self.evidence.to_dict()

        outcome = self.outcome.value

        provider_attempt_id = self.provider_attempt_id

        provider_code = self.provider_code

        subscription_id = self.subscription_id


        field_dict: dict[str, Any] = {}

        field_dict.update({
            "evidence": evidence,
            "outcome": outcome,
            "providerAttemptId": provider_attempt_id,
            "providerCode": provider_code,
            "subscriptionId": subscription_id,
        })

        return field_dict



    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.evidence import Evidence
        d = dict(src_dict)
        evidence = Evidence.from_dict(d.pop("evidence"))




        outcome = RecurringFailureCreateOutcome(d.pop("outcome"))




        provider_attempt_id = d.pop("providerAttemptId")

        provider_code = d.pop("providerCode")

        subscription_id = d.pop("subscriptionId")

        recurring_failure_create = cls(
            evidence=evidence,
            outcome=outcome,
            provider_attempt_id=provider_attempt_id,
            provider_code=provider_code,
            subscription_id=subscription_id,
        )

        return recurring_failure_create

