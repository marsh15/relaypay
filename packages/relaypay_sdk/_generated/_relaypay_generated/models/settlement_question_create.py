from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, BinaryIO, TextIO, TYPE_CHECKING, Generator

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

from ..types import UNSET, Unset
from typing import cast






T = TypeVar("T", bound="SettlementQuestionCreate")



@_attrs_define
class SettlementQuestionCreate:
    """ 
        Attributes:
            question (str):
            merchant_account_id (None | str | Unset):
     """

    question: str
    merchant_account_id: None | str | Unset = UNSET





    def to_dict(self) -> dict[str, Any]:
        question = self.question

        merchant_account_id: None | str | Unset
        if isinstance(self.merchant_account_id, Unset):
            merchant_account_id = UNSET
        else:
            merchant_account_id = self.merchant_account_id


        field_dict: dict[str, Any] = {}

        field_dict.update({
            "question": question,
        })
        if merchant_account_id is not UNSET:
            field_dict["merchantAccountId"] = merchant_account_id

        return field_dict



    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        question = d.pop("question")

        def _parse_merchant_account_id(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        merchant_account_id = _parse_merchant_account_id(d.pop("merchantAccountId", UNSET))


        settlement_question_create = cls(
            question=question,
            merchant_account_id=merchant_account_id,
        )

        return settlement_question_create

