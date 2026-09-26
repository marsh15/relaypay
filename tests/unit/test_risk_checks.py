from datetime import UTC, datetime

import pytest
from relaypay.risk_review.checks import (
    HARD_STOP_CLASSIFICATIONS,
    claims_risk,
    deterministic_checks,
    escalation_reason,
    is_hard_stop,
    severity_band,
)

NOW = datetime(2026, 9, 25, tzinfo=UTC)


def _snapshot(kind: str) -> dict[str, object]:
    from apps.merchant_site.main import _snapshot

    return _snapshot(kind)  # type: ignore[arg-type]


def test_clean_site_scores_zero_risk_with_low_severity() -> None:
    checks, breakdown = deterministic_checks(_snapshot("COMPLETE_CLEAN"), now=NOW)
    assert breakdown.total == 0
    assert severity_band(breakdown.total) == "LOW"
    assert not any(is_hard_stop(item.classification) for item in checks)


def test_deterministic_scores_are_reproducible_across_runs() -> None:
    for kind in (
        "COMPLETE_CLEAN",
        "MISSING_POLICIES",
        "YOUNG_DOMAIN",
        "PRICE_OUTLIER",
        "SUSPICIOUS_CLAIMS",
        "PROHIBITED_CATEGORY",
    ):
        first_checks, first = deterministic_checks(_snapshot(kind), now=NOW)
        second_checks, second = deterministic_checks(_snapshot(kind), now=NOW)
        assert first == second
        assert [item.classification for item in first_checks] == [
            item.classification for item in second_checks
        ]


@pytest.mark.parametrize(
    ("total", "expected"),
    [
        (0, "LOW"),
        (29, "LOW"),
        (30, "MEDIUM"),
        (59, "MEDIUM"),
        (60, "HIGH"),
        (79, "HIGH"),
        (80, "CRITICAL"),
        (100, "CRITICAL"),
    ],
)
def test_every_severity_threshold(total: int, expected: str) -> None:
    assert severity_band(total) == expected


def test_bucket_maxima_match_the_score_specification() -> None:
    snapshot = _snapshot("MISSING_POLICIES")
    checks, breakdown = deterministic_checks(snapshot, now=NOW)
    # The site keeps its HTML but loses contacts and both policies: 15 of 20.
    assert breakdown.completeness == 15
    assert breakdown.domain == 0
    assert breakdown.category == 0
    assert breakdown.pricing == 0
    assert breakdown.total == 15
    assert checks


def test_young_domain_flags_identity_mismatch_hard_stop() -> None:
    checks, breakdown = deterministic_checks(_snapshot("YOUNG_DOMAIN"), now=NOW)
    assert is_hard_stop("IMPERSONATION")
    assert any(
        item.check_key == "identity_consistency" and item.classification == "IMPERSONATION"
        for item in checks
    )
    assert breakdown.domain == 10  # age plus identity mismatch


def test_price_outlier_is_detected_deterministically() -> None:
    checks, _ = deterministic_checks(_snapshot("PRICE_OUTLIER"), now=NOW)
    outlier = next(item for item in checks if item.check_key == "pricing_outliers")
    assert outlier.classification == "UNREALISTIC_CLAIM"
    assert outlier.detail.startswith("1 catalogue prices")


def test_prohibited_category_and_counterfeit_are_hard_stops() -> None:
    checks, breakdown = deterministic_checks(_snapshot("PROHIBITED_CATEGORY"), now=NOW)
    classifications = {item.classification for item in checks}
    assert "PROHIBITED_CATEGORY" in classifications
    assert "COUNTERFEIT" in classifications
    # The replica catalogue still exists, so category risk is 20 of 30.
    assert breakdown.category == 20
    assert breakdown.total >= 20


def test_claims_risk_is_capped_and_linear() -> None:
    assert claims_risk(0) == 0
    assert claims_risk(1) == 5
    assert claims_risk(3) == 15
    assert claims_risk(9) == 15


def test_escalation_reason_prefers_hard_stops() -> None:
    assert escalation_reason("PROHIBITED_CATEGORY", "LOW") == "PROHIBITED_CATEGORY"
    assert escalation_reason("COUNTERFEIT", "LOW") == "COUNTERFEIT"
    assert escalation_reason("NONE", "CRITICAL") == "CRITICAL_SEVERITY"
    assert escalation_reason("NONE", "HIGH") == "HIGH_SEVERITY"
    assert escalation_reason("NONE", "MEDIUM") is None


def test_hard_stop_classification_set_is_exact() -> None:
    assert (
        frozenset({"PROHIBITED_CATEGORY", "IMPERSONATION", "COUNTERFEIT", "GUARANTEED_RETURN"})
        == HARD_STOP_CLASSIFICATIONS
    )
