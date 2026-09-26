from enum import Enum

class RiskReviewCreateSiteref(str, Enum):
    COMPLETE_CLEAN = "COMPLETE_CLEAN"
    MISSING_POLICIES = "MISSING_POLICIES"
    PRICE_OUTLIER = "PRICE_OUTLIER"
    PROHIBITED_CATEGORY = "PROHIBITED_CATEGORY"
    SUSPICIOUS_CLAIMS = "SUSPICIOUS_CLAIMS"
    YOUNG_DOMAIN = "YOUNG_DOMAIN"

    def __str__(self) -> str:
        return str(self.value)
