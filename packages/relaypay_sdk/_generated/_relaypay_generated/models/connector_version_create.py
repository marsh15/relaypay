from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, BinaryIO, TextIO, TYPE_CHECKING, Generator

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

from ..models.connector_version_create_kind import ConnectorVersionCreateKind
from ..types import UNSET, Unset
from typing import cast






T = TypeVar("T", bound="ConnectorVersionCreate")



@_attrs_define
class ConnectorVersionCreate:
    """ 
        Attributes:
            base_url (str):
            capabilities (list[str]):
            kind (ConnectorVersionCreateKind):
            reference (str):
            timeout_ms (int):
            credential_name (str | Unset):  Default: 'api_secret'.
     """

    base_url: str
    capabilities: list[str]
    kind: ConnectorVersionCreateKind
    reference: str
    timeout_ms: int
    credential_name: str | Unset = 'api_secret'





    def to_dict(self) -> dict[str, Any]:
        base_url = self.base_url

        capabilities = self.capabilities



        kind = self.kind.value

        reference = self.reference

        timeout_ms = self.timeout_ms

        credential_name = self.credential_name


        field_dict: dict[str, Any] = {}

        field_dict.update({
            "baseUrl": base_url,
            "capabilities": capabilities,
            "kind": kind,
            "reference": reference,
            "timeoutMs": timeout_ms,
        })
        if credential_name is not UNSET:
            field_dict["credentialName"] = credential_name

        return field_dict



    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        base_url = d.pop("baseUrl")

        capabilities = cast(list[str], d.pop("capabilities"))


        kind = ConnectorVersionCreateKind(d.pop("kind"))




        reference = d.pop("reference")

        timeout_ms = d.pop("timeoutMs")

        credential_name = d.pop("credentialName", UNSET)

        connector_version_create = cls(
            base_url=base_url,
            capabilities=capabilities,
            kind=kind,
            reference=reference,
            timeout_ms=timeout_ms,
            credential_name=credential_name,
        )

        return connector_version_create

