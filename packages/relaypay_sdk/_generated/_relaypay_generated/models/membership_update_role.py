from enum import Enum

class MembershipUpdateRole(str, Enum):
    DEVELOPER = "DEVELOPER"
    ORGANISATION_ADMIN = "ORGANISATION_ADMIN"
    VIEWER = "VIEWER"

    def __str__(self) -> str:
        return str(self.value)
