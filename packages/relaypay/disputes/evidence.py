from dataclasses import dataclass
from typing import Literal

from relaypay.disputes.models import DisputeCase
from relaypay.disputes.service import Citation, StructuredDisputeDraft


@dataclass(frozen=True, slots=True)
class EvidencePlan:
    selected: tuple[str, ...]
    missing: tuple[str, ...]


REQUIRED_EVIDENCE: dict[str, tuple[str, ...]] = {
    "FRAUD": ("paymentId", "authenticationResult", "customerCommunication"),
    "PRODUCT_NOT_RECEIVED": ("paymentId", "deliveryStatus", "deliveryProof"),
    "NOT_AS_DESCRIBED": ("paymentId", "invoice", "productDescription"),
    "DUPLICATE": ("paymentId", "relatedPaymentId"),
    "CREDIT_NOT_PROCESSED": ("paymentId", "refundStatus"),
    "OTHER": ("paymentId", "customerCommunication"),
}


def plan_evidence(case: DisputeCase) -> EvidencePlan:
    required = REQUIRED_EVIDENCE[case.reason_code]
    selected = tuple(field for field in required if field in case.source_snapshot)
    missing = tuple(field for field in required if field not in case.source_snapshot)
    return EvidencePlan(selected=selected, missing=missing)


def draft_from_allowlisted_evidence(case: DisputeCase) -> StructuredDisputeDraft:
    """Create a deterministic fake-model draft from the immutable, allowlisted snapshot."""
    plan = plan_evidence(case)
    confidence: Literal["LOW", "MEDIUM", "HIGH"] = (
        "HIGH" if not plan.missing else "MEDIUM" if len(plan.missing) == 1 else "LOW"
    )
    fields = plan.selected or ("$",)
    citation = Citation(
        recordType="dispute_case_snapshot",
        recordId=case.public_id,
        fieldPaths=list(fields),
        snapshotSha256=case.source_sha256.hex(),
    )
    missing_text = ", ".join(plan.missing) if plan.missing else "none"
    return StructuredDisputeDraft(
        classification=case.reason_code,
        confidence=confidence,
        missingEvidence=list(plan.missing),
        responseText=(
            f"The immutable RelayPay record supports classification {case.reason_code}. "
            f"Missing evidence: {missing_text}."
        ),
        citations=[citation],
    )
