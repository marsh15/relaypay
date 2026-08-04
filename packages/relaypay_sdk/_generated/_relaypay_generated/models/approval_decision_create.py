from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, BinaryIO, TextIO, TYPE_CHECKING, Generator

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

from ..models.approval_decision_create_decision import ApprovalDecisionCreateDecision
from ..types import UNSET, Unset
from typing import cast






T = TypeVar("T", bound="ApprovalDecisionCreate")



@_attrs_define
class ApprovalDecisionCreate:
    """ 
        Attributes:
            decision (ApprovalDecisionCreateDecision):
            note (None | str | Unset):
     """

    decision: ApprovalDecisionCreateDecision
    note: None | str | Unset = UNSET





    def to_dict(self) -> dict[str, Any]:
        decision = self.decision.value

        note: None | str | Unset
        if isinstance(self.note, Unset):
            note = UNSET
        else:
            note = self.note


        field_dict: dict[str, Any] = {}

        field_dict.update({
            "decision": decision,
        })
        if note is not UNSET:
            field_dict["note"] = note

        return field_dict



    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        decision = ApprovalDecisionCreateDecision(d.pop("decision"))




        def _parse_note(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        note = _parse_note(d.pop("note", UNSET))


        approval_decision_create = cls(
            decision=decision,
            note=note,
        )

        return approval_decision_create

