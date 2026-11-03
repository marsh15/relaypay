from enum import StrEnum

class ConnectorVerifyKind(StrEnum):
    BANK = "BANK"
    PAYMENT = "PAYMENT"

    def __str__(self) -> str:
        return str(self.value)
