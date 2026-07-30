from enum import Enum

class ConnectorVersionCreateKind(str, Enum):
    BANK = "BANK"
    COMMERCE = "COMMERCE"
    PAYMENT = "PAYMENT"

    def __str__(self) -> str:
        return str(self.value)
