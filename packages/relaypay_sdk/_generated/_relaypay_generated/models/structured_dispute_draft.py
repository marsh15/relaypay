from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, BinaryIO, TextIO, TYPE_CHECKING, Generator

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

from ..models.structured_dispute_draft_confidence import StructuredDisputeDraftConfidence
from typing import cast

if TYPE_CHECKING:
  from ..models.citation import Citation





T = TypeVar("T", bound="StructuredDisputeDraft")



@_attrs_define
class StructuredDisputeDraft:
    """ 
        Attributes:
            citations (list[Citation]):
            classification (str):
            confidence (StructuredDisputeDraftConfidence):
            missing_evidence (list[str]):
            response_text (str):
     """

    citations: list[Citation]
    classification: str
    confidence: StructuredDisputeDraftConfidence
    missing_evidence: list[str]
    response_text: str





    def to_dict(self) -> dict[str, Any]:
        from ..models.citation import Citation
        citations = []
        for citations_item_data in self.citations:
            citations_item = citations_item_data.to_dict()
            citations.append(citations_item)



        classification = self.classification

        confidence = self.confidence.value

        missing_evidence = self.missing_evidence



        response_text = self.response_text


        field_dict: dict[str, Any] = {}

        field_dict.update({
            "citations": citations,
            "classification": classification,
            "confidence": confidence,
            "missingEvidence": missing_evidence,
            "responseText": response_text,
        })

        return field_dict



    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.citation import Citation
        d = dict(src_dict)
        citations = []
        _citations = d.pop("citations")
        for citations_item_data in (_citations):
            citations_item = Citation.from_dict(citations_item_data)



            citations.append(citations_item)


        classification = d.pop("classification")

        confidence = StructuredDisputeDraftConfidence(d.pop("confidence"))




        missing_evidence = cast(list[str], d.pop("missingEvidence"))


        response_text = d.pop("responseText")

        structured_dispute_draft = cls(
            citations=citations,
            classification=classification,
            confidence=confidence,
            missing_evidence=missing_evidence,
            response_text=response_text,
        )

        return structured_dispute_draft

