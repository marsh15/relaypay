from enum import Enum

class SettlementPolicyCreateWeekendhandling(str, Enum):
    INCLUDE = "INCLUDE"
    SKIP = "SKIP"

    def __str__(self) -> str:
        return str(self.value)
