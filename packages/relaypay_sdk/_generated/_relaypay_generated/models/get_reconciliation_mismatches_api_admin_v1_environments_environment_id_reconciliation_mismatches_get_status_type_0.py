from enum import StrEnum

class GetReconciliationMismatchesApiAdminV1EnvironmentsEnvironmentIdReconciliationMismatchesGetStatusType0(StrEnum):
    ACKNOWLEDGED = "ACKNOWLEDGED"
    OPEN = "OPEN"
    RESOLVED = "RESOLVED"

    def __str__(self) -> str:
        return str(self.value)
