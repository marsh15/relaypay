from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, BinaryIO, TextIO, TYPE_CHECKING, Generator

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

from ..models.risk_review_create_siteref import RiskReviewCreateSiteref






T = TypeVar("T", bound="RiskReviewCreate")



@_attrs_define
class RiskReviewCreate:
    """ 
        Attributes:
            site_ref (RiskReviewCreateSiteref):
     """

    site_ref: RiskReviewCreateSiteref





    def to_dict(self) -> dict[str, Any]:
        site_ref = self.site_ref.value


        field_dict: dict[str, Any] = {}

        field_dict.update({
            "siteRef": site_ref,
        })

        return field_dict



    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        site_ref = RiskReviewCreateSiteref(d.pop("siteRef"))




        risk_review_create = cls(
            site_ref=site_ref,
        )

        return risk_review_create

