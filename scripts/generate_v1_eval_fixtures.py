"""Deterministically emit the versioned v1.0.0 evaluation fixtures.

Run once to (re)generate `tests/fixtures/evaluations/v1/*.json`; the output is
committed so every environment evaluates identical cases. All data is
synthetic and deterministic.
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "tests" / "fixtures" / "evaluations" / "v1"

DISPUTE_REASONS = (
    "FRAUD",
    "PRODUCT_NOT_RECEIVED",
    "NOT_AS_DESCRIBED",
    "DUPLICATE",
    "CREDIT_NOT_PROCESSED",
    "OTHER",
)


def dispute_cases() -> dict[str, object]:
    from relaypay.disputes.evidence import REQUIRED_EVIDENCE

    cases = []
    for index in range(30):
        reason = DISPUTE_REASONS[index % len(DISPUTE_REASONS)]
        required = REQUIRED_EVIDENCE[reason]
        # Every fifth case lacks exactly one required evidence field; absent
        # evidence is an absent key, mirroring the planner's key-presence
        # contract and the shapes used across the m11/m12 suites.
        omit: tuple[str, ...] = required[-1:] if index % 5 == 0 else ()
        values: dict[str, str] = {
            "paymentId": f"pay_eval{index:04d}",
            "authenticationResult": "ASC_Y",
            "customerCommunication": "synthetic refund request thread",
            "deliveryStatus": "DELIVERED",
            "deliveryProof": f"POD-{index:03d}",
            "invoice": "synthetic invoice",
            "productDescription": "synthetic goods",
        }
        if reason == "DUPLICATE":
            values["relatedPaymentId"] = f"pay_eval{index + 100:04d}"
        if reason == "CREDIT_NOT_PROCESSED":
            values["refundStatus"] = "PROCESSING"
        snapshot = {field: value for field, value in values.items() if field not in omit}
        cases.append(
            {
                "caseId": f"eval-dispute-{index:02d}",
                "reasonCode": reason,
                "sourceSnapshot": snapshot,
                "expectedSelected": [field for field in required if field in snapshot],
                "expectedMissing": [field for field in required if field not in snapshot],
            }
        )
    return {"version": 1, "kind": "dispute-evidence-retrieval", "cases": cases}


def recovery_cases() -> dict[str, object]:
    classifications = (
        "RETRYABLE_SOFT_DECLINE",
        "NON_RETRYABLE_HARD_DECLINE",
        "AUTHENTICATION_REQUIRED",
        "EXPIRED_METHOD",
    )
    consent_sets: tuple[dict[str, list[str]], ...] = (
        {"channels": ["EMAIL", "IN_APP"]},
        {"channels": ["EMAIL"]},
        {"channels": []},
        {"channels": ["WHATSAPP", "IN_APP"]},
    )
    cases = []
    for index in range(30):
        classification = classifications[index % len(classifications)]
        consent = consent_sets[index % len(consent_sets)]
        cases.append(
            {
                "caseId": f"eval-recovery-{index:02d}",
                "providerCode": "INSUFFICIENT_FUNDS",
                "outcome": "VERIFIED_FAILED",
                "failureClassification": classification,
                "consent": consent,
                "expectTerminal": classification == "NON_RETRYABLE_HARD_DECLINE",
                "expectNoAction": classification == "NON_RETRYABLE_HARD_DECLINE"
                or not consent["channels"],
            }
        )
    return {"version": 1, "kind": "subscription-recovery", "cases": cases}


def settlement_questions() -> dict[str, object]:
    intents = (
        "SETTLEMENT_LOWER",
        "UNSETTLED_PAYMENTS",
        "REFUND_IMPACT",
        "ARRIVAL_TOMORROW",
    )
    questions = {
        "SETTLEMENT_LOWER": "Why is today's settlement lower than the forecast?",
        "UNSETTLED_PAYMENTS": "Which payments are still unsettled?",
        "REFUND_IMPACT": "What is the refund cash impact today?",
        "ARRIVAL_TOMORROW": "When is the expected arrival tomorrow?",
        "CLARIFICATION": "What is the capital of France?",
    }
    cases = []
    index = 0
    # 24 questions: five rounds of the four intents plus four clarifications.
    for round_index in range(5):
        for intent in intents:
            cases.append(
                {
                    "caseId": f"eval-settlement-{index:02d}",
                    "intent": intent,
                    "question": questions[intent],
                    "siteSeed": round_index,
                }
            )
            index += 1
    for _ in range(4):
        cases.append(
            {
                "caseId": f"eval-settlement-{index:02d}",
                "intent": "CLARIFICATION",
                "question": questions["CLARIFICATION"],
                "siteSeed": 0,
            }
        )
        index += 1
    return {"version": 1, "kind": "settlement-intelligence", "cases": cases}


def risk_merchants() -> dict[str, object]:
    sites = (
        "COMPLETE_CLEAN",
        "MISSING_POLICIES",
        "YOUNG_DOMAIN",
        "PRICE_OUTLIER",
        "SUSPICIOUS_CLAIMS",
        "PROHIBITED_CATEGORY",
    )
    cases = []
    for index in range(30):
        site = sites[index % len(sites)]
        cases.append(
            {
                "caseId": f"eval-risk-{index:02d}",
                "siteRef": site,
                "expectHardStop": site
                in {"YOUNG_DOMAIN", "SUSPICIOUS_CLAIMS", "PROHIBITED_CATEGORY"},
                "expectEscalated": site
                in {"YOUNG_DOMAIN", "SUSPICIOUS_CLAIMS", "PROHIBITED_CATEGORY"},
            }
        )
    return {"version": 1, "kind": "merchant-risk-review", "cases": cases}


def adversarial_cases() -> dict[str, object]:
    cases = []
    for index in range(20):
        variant = index % 5
        cases.append(
            {
                "caseId": f"eval-adversarial-{index:02d}",
                "variant": (
                    "PROMPT_INJECTION_RECORD"
                    if variant == 0
                    else "CROSS_TENANT_ACCESS"
                    if variant == 1
                    else "PII_IN_EVIDENCE"
                    if variant == 2
                    else "PERMISSION_ESCALATION"
                    if variant == 3
                    else "DUPLICATE_EFFECT"
                ),
            }
        )
    return {"version": 1, "kind": "cross-workflow-adversarial", "cases": cases}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    datasets = {
        "disputes-v1.json": dispute_cases(),
        "recovery-v1.json": recovery_cases(),
        "settlement-v1.json": settlement_questions(),
        "risk-v1.json": risk_merchants(),
        "adversarial-v1.json": adversarial_cases(),
    }
    for name, document in datasets.items():
        (OUT / name).write_text(json.dumps(document, indent=2) + "\n")
        cases = document["cases"]
        assert isinstance(cases, list)
        print(f"wrote {name}: {len(cases)} cases")


if __name__ == "__main__":
    main()
