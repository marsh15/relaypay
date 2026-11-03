from enum import StrEnum

class SettlementPolicyCreateWeekendhandling(StrEnum):
    INCLUDE = "INCLUDE"
    SKIP = "SKIP"

    def __str__(self) -> str:
        return str(self.value)
