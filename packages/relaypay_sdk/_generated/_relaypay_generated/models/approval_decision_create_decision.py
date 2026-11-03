from enum import StrEnum

class ApprovalDecisionCreateDecision(StrEnum):
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"

    def __str__(self) -> str:
        return str(self.value)
