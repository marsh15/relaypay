from enum import Enum

class ApprovalDecisionCreateDecision(str, Enum):
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"

    def __str__(self) -> str:
        return str(self.value)
