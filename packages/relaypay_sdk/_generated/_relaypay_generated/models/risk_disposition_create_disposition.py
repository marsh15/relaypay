from enum import Enum

class RiskDispositionCreateDisposition(str, Enum):
    CLOSE_NO_ACTION = "CLOSE_NO_ACTION"
    REJECT_ONBOARDING = "REJECT_ONBOARDING"
    REQUEST_DOCUMENTS = "REQUEST_DOCUMENTS"

    def __str__(self) -> str:
        return str(self.value)
