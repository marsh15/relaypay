from enum import StrEnum

class MembershipUpdateStatus(StrEnum):
    ACTIVE = "ACTIVE"
    DISABLED = "DISABLED"

    def __str__(self) -> str:
        return str(self.value)
