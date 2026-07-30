from enum import Enum

class GetReconciliationMismatchesApiAdminV1EnvironmentsEnvironmentIdReconciliationMismatchesGetStatusType0(str, Enum):
    ACKNOWLEDGED = "ACKNOWLEDGED"
    OPEN = "OPEN"
    RESOLVED = "RESOLVED"

    def __str__(self) -> str:
        return str(self.value)
