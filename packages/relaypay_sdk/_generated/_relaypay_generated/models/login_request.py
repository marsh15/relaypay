from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, BinaryIO, TextIO, TYPE_CHECKING, Generator

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

from ..types import UNSET, Unset
from typing import cast






T = TypeVar("T", bound="LoginRequest")



@_attrs_define
class LoginRequest:
    """ 
        Attributes:
            email (str):
            password (str):
            organisation_id (None | str | Unset):
     """

    email: str
    password: str
    organisation_id: None | str | Unset = UNSET





    def to_dict(self) -> dict[str, Any]:
        email = self.email

        password = self.password

        organisation_id: None | str | Unset
        if isinstance(self.organisation_id, Unset):
            organisation_id = UNSET
        else:
            organisation_id = self.organisation_id


        field_dict: dict[str, Any] = {}

        field_dict.update({
            "email": email,
            "password": password,
        })
        if organisation_id is not UNSET:
            field_dict["organisationId"] = organisation_id

        return field_dict



    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        email = d.pop("email")

        password = d.pop("password")

        def _parse_organisation_id(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        organisation_id = _parse_organisation_id(d.pop("organisationId", UNSET))


        login_request = cls(
            email=email,
            password=password,
            organisation_id=organisation_id,
        )

        return login_request

