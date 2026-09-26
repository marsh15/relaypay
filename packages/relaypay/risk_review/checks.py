"""Deterministic merchant risk checks and the versioned scoring model v1.

Every check is a pure function over the immutable snapshot. Scores, severity,
confidence, hard stops, and escalation reasons are computed here and are
reproducible byte-for-byte; models never assign scores.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

SCORE_VERSION = 1
MINIMUM_DOMAIN_AGE_YEARS = 1
PRICING_OUTLIER_FACTOR = 20
HARD_STOP_CLASSIFICATIONS = frozenset(
    {"PROHIBITED_CATEGORY", "IMPERSONATION", "COUNTERFEIT", "GUARANTEED_RETURN"}
)
MODEL_FINDING_TYPES = frozenset(
    {
        "SUSPICIOUS_LANGUAGE",
        "UNREALISTIC_CLAIM",
        "GUARANTEED_RETURN",
        "PROHIBITED_CATEGORY",
        "IMPERSONATION",
        "COUNTERFEIT",
    }
)
MAX_SCORE = 100


@dataclass(frozen=True, slots=True)
class CheckResult:
    check_key: str
    classification: str
    severity: str
    confidence: str
    detail: str
    earned: int
    maximum: int


@dataclass(frozen=True, slots=True)
class ScoreBreakdown:
    completeness: int
    domain: int
    category: int
    pricing: int
    claims: int

    @property
    def total(self) -> int:
        return self.completeness + self.domain + self.category + self.pricing + self.claims


def severity_for_total(total: int) -> str:
    if total >= 80:
        return "CRITICAL"
    if total >= 60:
        return "HIGH"
    if total >= 30:
        return "MEDIUM"
    return "LOW"


def confidence_for(sources: int, *, model_only: bool = False) -> str:
    """HIGH for deterministic evidence or two-source corroboration; LOW model-only."""
    if sources >= 2:
        return "HIGH"
    if model_only:
        return "LOW"
    return "MEDIUM"


def _site_present(snapshot: dict[str, object]) -> bool:
    html = snapshot.get("html")
    if not isinstance(html, dict):
        return False
    body = html.get("body")
    title = html.get("title")
    return isinstance(body, str) and len(body) > 40 and isinstance(title, str) and bool(title)


def check_completeness(snapshot: dict[str, object]) -> list[CheckResult]:
    results: list[CheckResult] = []
    contact = snapshot.get("contact") if isinstance(snapshot.get("contact"), dict) else {}
    assert isinstance(contact, dict)
    policies = snapshot.get("policies") if isinstance(snapshot.get("policies"), dict) else {}
    assert isinstance(policies, dict)
    results.append(
        CheckResult(
            "website_present",
            "NONE" if _site_present(snapshot) else "SUSPICIOUS_LANGUAGE",
            "NONE" if _site_present(snapshot) else "HIGH",
            "HIGH",
            "Website HTML and title are present."
            if _site_present(snapshot)
            else "Website HTML or title is missing or too short.",
            5 if _site_present(snapshot) else 0,
            5,
        )
    )
    contact_fields = ["supportEmail", "phone", "postalAddress"]
    present = sum(
        1 for field in contact_fields if isinstance(contact.get(field), str) and contact.get(field)
    )
    results.append(
        CheckResult(
            "contact_details",
            "NONE" if present == len(contact_fields) else "SUSPICIOUS_LANGUAGE",
            "NONE" if present == len(contact_fields) else "MEDIUM",
            "HIGH",
            f"{present} of {len(contact_fields)} contact channels are present.",
            5 if present == len(contact_fields) else (2 if present > 0 else 0),
            5,
        )
    )
    for key, label in (("refund", "Refund policy"), ("privacy", "Privacy policy")):
        value = policies.get(key)
        ok = isinstance(value, str) and len(value) >= 40
        results.append(
            CheckResult(
                f"{key}_policy",
                "NONE" if ok else "SUSPICIOUS_LANGUAGE",
                "NONE" if ok else "MEDIUM",
                "HIGH",
                f"{label} is present." if ok else f"{label} is missing or too short.",
                5 if ok else 0,
                5,
            )
        )
    return results


def check_domain(snapshot: dict[str, object], *, now: datetime) -> list[CheckResult]:
    results: list[CheckResult] = []
    whois = snapshot.get("whois") if isinstance(snapshot.get("whois"), dict) else {}
    identity = snapshot.get("identity") if isinstance(snapshot.get("identity"), dict) else {}
    assert isinstance(whois, dict) and isinstance(identity, dict)
    domain = whois.get("domain")
    results.append(
        CheckResult(
            "whois_present",
            "NONE" if isinstance(domain, str) and domain else "SUSPICIOUS_LANGUAGE",
            "NONE" if isinstance(domain, str) and domain else "MEDIUM",
            "HIGH",
            "WHOIS-like domain data is present."
            if domain
            else "WHOIS-like domain data is missing.",
            5 if domain else 0,
            5,
        )
    )
    created_raw = whois.get("createdAt")
    age_years: float | None = None
    if isinstance(created_raw, str):
        try:
            created = datetime.fromisoformat(created_raw.replace("Z", "+00:00"))
            age_years = (now - created).days / 365.25
        except ValueError:
            age_years = None
    old_enough = age_years is not None and age_years >= MINIMUM_DOMAIN_AGE_YEARS
    results.append(
        CheckResult(
            "domain_age",
            "NONE" if old_enough else "UNREALISTIC_CLAIM",
            "NONE" if old_enough else "MEDIUM",
            "HIGH",
            (
                f"Domain was created {age_years:.1f} synthetic years ago."
                if age_years is not None
                else "Domain creation date is missing or unreadable."
            ),
            5 if old_enough else 0,
            5,
        )
    )
    country = whois.get("registrantCountry")
    results.append(
        CheckResult(
            "registrant_identity",
            "NONE" if isinstance(country, str) and country else "SUSPICIOUS_LANGUAGE",
            "NONE" if isinstance(country, str) and country else "MEDIUM",
            "HIGH",
            "Registrant country is recorded." if country else "Registrant country is missing.",
            5 if country else 0,
            5,
        )
    )
    merchant = identity.get("merchantName")
    organization = whois.get("registrantOrganization")
    consistent = (
        isinstance(merchant, str)
        and isinstance(organization, str)
        and bool(merchant)
        and (
            merchant.casefold() in organization.casefold()
            or organization.casefold() in merchant.casefold()
        )
    )
    results.append(
        CheckResult(
            "identity_consistency",
            "NONE" if consistent else "IMPERSONATION",
            "NONE" if consistent else "MEDIUM",
            "HIGH",
            "Merchant name matches the registrant organization."
            if consistent
            else "Merchant name does not match the WHOIS registrant organization.",
            5 if consistent else 0,
            5,
        )
    )
    return results


def check_category(snapshot: dict[str, object]) -> list[CheckResult]:
    results: list[CheckResult] = []
    category = snapshot.get("category")
    known = isinstance(category, str) and category == "HOME_GOODS"
    results.append(
        CheckResult(
            "merchant_category",
            "PROHIBITED_CATEGORY"
            if isinstance(category, str) and category != "HOME_GOODS"
            else "NONE",
            "NONE" if known else "CRITICAL",
            "HIGH",
            f"Merchant category is {category}.",
            10 if known else 0,
            10,
        )
    )
    catalogue = snapshot.get("catalogue") if isinstance(snapshot.get("catalogue"), dict) else {}
    assert isinstance(catalogue, dict)
    raw_items = catalogue.get("items")
    items: list[object] = list(raw_items) if isinstance(raw_items, list) else []
    titles = [
        str(item.get("title", ""))
        for item in items
        if isinstance(item, dict) and isinstance(item.get("title"), str)
    ]
    replica_language = any("replica" in title.casefold() for title in titles)
    content_ok = known and not replica_language
    results.append(
        CheckResult(
            "content_matches_category",
            "COUNTERFEIT" if replica_language else "NONE",
            "NONE" if content_ok else "CRITICAL",
            "HIGH",
            "Catalogue content matches the declared category."
            if content_ok
            else "Catalogue content does not match the declared category.",
            10 if content_ok else 0,
            10,
        )
    )
    results.append(
        CheckResult(
            "catalogue_present",
            "NONE" if items else "SUSPICIOUS_LANGUAGE",
            "NONE" if items else "MEDIUM",
            "HIGH",
            "Catalogue items are present." if items else "Catalogue is empty or missing.",
            10 if items else 0,
            10,
        )
    )
    return results


def check_pricing(snapshot: dict[str, object]) -> list[CheckResult]:
    results: list[CheckResult] = []
    catalogue = snapshot.get("catalogue") if isinstance(snapshot.get("catalogue"), dict) else {}
    assert isinstance(catalogue, dict)
    raw_items = catalogue.get("items")
    items: list[object] = list(raw_items) if isinstance(raw_items, list) else []
    prices = [
        int(item["pricePaise"])
        for item in items
        if isinstance(item, dict)
        and isinstance(item.get("pricePaise"), int)
        and item["pricePaise"] > 0
    ]
    results.append(
        CheckResult(
            "pricing_present",
            "NONE" if prices else "SUSPICIOUS_LANGUAGE",
            "NONE" if prices else "MEDIUM",
            "HIGH",
            f"{len(prices)} catalogue prices are present.",
            5 if prices else 0,
            5,
        )
    )
    median = catalogue.get("medianPricePaise")
    outliers = 0
    if isinstance(median, int) and median > 0 and prices:
        outliers = sum(1 for price in prices if price > median * PRICING_OUTLIER_FACTOR)
    results.append(
        CheckResult(
            "pricing_outliers",
            "UNREALISTIC_CLAIM" if outliers else "NONE",
            "MEDIUM" if outliers else "NONE",
            "HIGH",
            (
                f"{outliers} catalogue prices exceed the median by more than "
                f"{PRICING_OUTLIER_FACTOR}x."
                if outliers
                else "No catalogue price is an extreme outlier of the median."
            ),
            10 if not outliers else 0,
            10,
        )
    )
    return results


def deterministic_checks(
    snapshot: dict[str, object], *, now: datetime
) -> tuple[list[CheckResult], ScoreBreakdown]:
    results = [
        *check_completeness(snapshot),
        *check_domain(snapshot, now=now),
        *check_category(snapshot),
        *check_pricing(snapshot),
    ]

    def risk_for(keys: tuple[str, ...], maximum: int) -> int:
        return maximum - sum(item.earned for item in results if item.check_key in keys)

    return results, ScoreBreakdown(
        completeness=risk_for(
            ("website_present", "contact_details", "refund_policy", "privacy_policy"), 20
        ),
        domain=risk_for(
            ("whois_present", "domain_age", "registrant_identity", "identity_consistency"), 20
        ),
        category=risk_for(
            ("merchant_category", "content_matches_category", "catalogue_present"), 30
        ),
        pricing=risk_for(("pricing_present", "pricing_outliers"), 15),
        claims=0,
    )


def claims_risk(findings: int) -> int:
    """Each corroborated or single-source model claim adds five risk points."""
    return min(15, 5 * findings)


def severity_band(total: int) -> str:
    return severity_for_total(total)


def is_hard_stop(classification: str) -> bool:
    return classification in HARD_STOP_CLASSIFICATIONS


def escalation_reason(classification: str, severity: str) -> str | None:
    if classification == "PROHIBITED_CATEGORY":
        return "PROHIBITED_CATEGORY"
    if classification == "IMPERSONATION":
        return "IMPERSONATION"
    if classification == "COUNTERFEIT":
        return "COUNTERFEIT"
    if classification == "GUARANTEED_RETURN":
        return "GUARANTEED_RETURN"
    if severity == "CRITICAL":
        return "CRITICAL_SEVERITY"
    if severity == "HIGH":
        return "HIGH_SEVERITY"
    return None


def utc_now() -> datetime:
    return datetime.now(UTC)
