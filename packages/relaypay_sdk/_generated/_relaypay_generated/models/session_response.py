from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, BinaryIO, TextIO, TYPE_CHECKING, Generator

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

from ..types import UNSET, Unset
from typing import cast






T = TypeVar("T", bound="SessionResponse")



@_attrs_define
class SessionResponse:
    """ 
        Attributes:
            csrf_token (str):
            display_name (str):
            organisation_id (str):
            organisation_role (None | str):
            platform_role (str):
            user_id (str):
            expires_at (None | str | Unset):
     """

    csrf_token: str
    display_name: str
    organisation_id: str
    organisation_role: None | str
    platform_role: str
    user_id: str
    expires_at: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)





    def to_dict(self) -> dict[str, Any]:
        csrf_token = self.csrf_token

        display_name = self.display_name

        organisation_id = self.organisation_id

        organisation_role: None | str
        organisation_role = self.organisation_role

        platform_role = self.platform_role

        user_id = self.user_id

        expires_at: None | str | Unset
        if isinstance(self.expires_at, Unset):
            expires_at = UNSET
        else:
            expires_at = self.expires_at


        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({
            "csrfToken": csrf_token,
            "displayName": display_name,
            "organisationId": organisation_id,
            "organisationRole": organisation_role,
            "platformRole": platform_role,
            "userId": user_id,
        })
        if expires_at is not UNSET:
            field_dict["expiresAt"] = expires_at

        return field_dict



    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        csrf_token = d.pop("csrfToken")

        display_name = d.pop("displayName")

        organisation_id = d.pop("organisationId")

        def _parse_organisation_role(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        organisation_role = _parse_organisation_role(d.pop("organisationRole"))


        platform_role = d.pop("platformRole")

        user_id = d.pop("userId")

        def _parse_expires_at(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        expires_at = _parse_expires_at(d.pop("expiresAt", UNSET))


        session_response = cls(
            csrf_token=csrf_token,
            display_name=display_name,
            organisation_id=organisation_id,
            organisation_role=organisation_role,
            platform_role=platform_role,
            user_id=user_id,
            expires_at=expires_at,
        )


        session_response.additional_properties = d
        return session_response

    @property
    def additional_keys(self) -> list[str]:
        return list(self.additional_properties.keys())

    def __getitem__(self, key: str) -> Any:
        return self.additional_properties[key]

    def __setitem__(self, key: str, value: Any) -> None:
        self.additional_properties[key] = value

    def __delitem__(self, key: str) -> None:
        del self.additional_properties[key]

    def __contains__(self, key: str) -> bool:
        return key in self.additional_properties
