from enum import Enum

class ConnectorVerifyKind(str, Enum):
    BANK = "BANK"
    PAYMENT = "PAYMENT"

    def __str__(self) -> str:
        return str(self.value)
