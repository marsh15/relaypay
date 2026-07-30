from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, BinaryIO, TextIO, TYPE_CHECKING, Generator

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

from typing import Literal, cast






T = TypeVar("T", bound="ScenarioCreate")



@_attrs_define
class ScenarioCreate:
    """ 
        Attributes:
            scenario_type (Literal['LOST_CAPTURE_RESPONSE']):
     """

    scenario_type: Literal['LOST_CAPTURE_RESPONSE']





    def to_dict(self) -> dict[str, Any]:
        scenario_type = self.scenario_type


        field_dict: dict[str, Any] = {}

        field_dict.update({
            "scenarioType": scenario_type,
        })

        return field_dict



    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        scenario_type = cast(Literal['LOST_CAPTURE_RESPONSE'] , d.pop("scenarioType"))
        if scenario_type != 'LOST_CAPTURE_RESPONSE':
            raise ValueError(f"scenarioType must match const 'LOST_CAPTURE_RESPONSE', got '{scenario_type}'")

        scenario_create = cls(
            scenario_type=scenario_type,
        )

        return scenario_create

