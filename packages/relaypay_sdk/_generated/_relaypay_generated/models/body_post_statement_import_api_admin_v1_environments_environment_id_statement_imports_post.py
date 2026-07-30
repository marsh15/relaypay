from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, BinaryIO, TextIO, TYPE_CHECKING, Generator

from attrs import define as _attrs_define
from attrs import field as _attrs_field
import json
from .. import types

from ..types import UNSET, Unset

from ..models.body_post_statement_import_api_admin_v1_environments_environment_id_statement_imports_post_sourceformat import BodyPostStatementImportApiAdminV1EnvironmentsEnvironmentIdStatementImportsPostSourceformat
from typing import cast
from typing import Literal, cast
import datetime






T = TypeVar("T", bound="BodyPostStatementImportApiAdminV1EnvironmentsEnvironmentIdStatementImportsPost")



@_attrs_define
class BodyPostStatementImportApiAdminV1EnvironmentsEnvironmentIdStatementImportsPost:
    """ 
        Attributes:
            period_end (datetime.datetime):
            period_start (datetime.datetime):
            provider (Literal['PAYMENT_PROVIDER']):
            source_format (BodyPostStatementImportApiAdminV1EnvironmentsEnvironmentIdStatementImportsPostSourceformat):
            source_reference (str):
            statement (str):
     """

    period_end: datetime.datetime
    period_start: datetime.datetime
    provider: Literal['PAYMENT_PROVIDER']
    source_format: BodyPostStatementImportApiAdminV1EnvironmentsEnvironmentIdStatementImportsPostSourceformat
    source_reference: str
    statement: str
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)





    def to_dict(self) -> dict[str, Any]:
        period_end = self.period_end.isoformat()

        period_start = self.period_start.isoformat()

        provider = self.provider

        source_format = self.source_format.value

        source_reference = self.source_reference

        statement = self.statement


        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({
            "periodEnd": period_end,
            "periodStart": period_start,
            "provider": provider,
            "sourceFormat": source_format,
            "sourceReference": source_reference,
            "statement": statement,
        })

        return field_dict


    def to_multipart(self) -> types.RequestFiles:
        files: types.RequestFiles = []

        files.append(("periodEnd", (None, self.period_end.isoformat().encode(), "text/plain")))



        files.append(("periodStart", (None, self.period_start.isoformat().encode(), "text/plain")))



        files.append(("provider", (None, self.provider, "text/plain")))



        files.append(("sourceFormat",  (None, str(self.source_format.value).encode(), "text/plain")))



        files.append(("sourceReference", (None, str(self.source_reference).encode(), "text/plain")))



        files.append(("statement", (None, str(self.statement).encode(), "text/plain")))




        for prop_name, prop in self.additional_properties.items():
            files.append((prop_name, (None, str(prop).encode(), "text/plain")))



        return files


    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        period_end = datetime.datetime.fromisoformat(d.pop("periodEnd"))




        period_start = datetime.datetime.fromisoformat(d.pop("periodStart"))




        provider = cast(Literal['PAYMENT_PROVIDER'] , d.pop("provider"))
        if provider != 'PAYMENT_PROVIDER':
            raise ValueError(f"provider must match const 'PAYMENT_PROVIDER', got '{provider}'")

        source_format = BodyPostStatementImportApiAdminV1EnvironmentsEnvironmentIdStatementImportsPostSourceformat(d.pop("sourceFormat"))




        source_reference = d.pop("sourceReference")

        statement = d.pop("statement")

        body_post_statement_import_api_admin_v1_environments_environment_id_statement_imports_post = cls(
            period_end=period_end,
            period_start=period_start,
            provider=provider,
            source_format=source_format,
            source_reference=source_reference,
            statement=statement,
        )


        body_post_statement_import_api_admin_v1_environments_environment_id_statement_imports_post.additional_properties = d
        return body_post_statement_import_api_admin_v1_environments_environment_id_statement_imports_post

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
