from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, BinaryIO, TextIO, TYPE_CHECKING, Generator

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

from ..models.membership_update_role import MembershipUpdateRole
from ..models.membership_update_status import MembershipUpdateStatus
from ..types import UNSET, Unset






T = TypeVar("T", bound="MembershipUpdate")



@_attrs_define
class MembershipUpdate:
    """ 
        Attributes:
            email (str):
            role (MembershipUpdateRole):
            status (MembershipUpdateStatus | Unset):  Default: MembershipUpdateStatus.ACTIVE.
     """

    email: str
    role: MembershipUpdateRole
    status: MembershipUpdateStatus | Unset = MembershipUpdateStatus.ACTIVE





    def to_dict(self) -> dict[str, Any]:
        email = self.email

        role = self.role.value

        status: str | Unset = UNSET
        if not isinstance(self.status, Unset):
            status = self.status.value



        field_dict: dict[str, Any] = {}

        field_dict.update({
            "email": email,
            "role": role,
        })
        if status is not UNSET:
            field_dict["status"] = status

        return field_dict



    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        email = d.pop("email")

        role = MembershipUpdateRole(d.pop("role"))




        _status = d.pop("status", UNSET)
        status: MembershipUpdateStatus | Unset
        if isinstance(_status,  Unset):
            status = UNSET
        else:
            status = MembershipUpdateStatus(_status)




        membership_update = cls(
            email=email,
            role=role,
            status=status,
        )

        return membership_update

