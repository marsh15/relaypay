from enum import Enum

class MembershipUpdateRole(str, Enum):
    APPROVER = "APPROVER"
    DEVELOPER = "DEVELOPER"
    OPERATIONS_ANALYST = "OPERATIONS_ANALYST"
    ORGANISATION_ADMIN = "ORGANISATION_ADMIN"
    VIEWER = "VIEWER"

    def __str__(self) -> str:
        return str(self.value)
