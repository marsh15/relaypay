from enum import StrEnum

class RecurringFailureCreateOutcome(StrEnum):
    TRANSPORT_UNKNOWN = "TRANSPORT_UNKNOWN"
    VERIFIED_FAILED = "VERIFIED_FAILED"

    def __str__(self) -> str:
        return str(self.value)
