from enum import StrEnum

class ConnectorVersionCreateKind(StrEnum):
    BANK = "BANK"
    COMMERCE = "COMMERCE"
    PAYMENT = "PAYMENT"

    def __str__(self) -> str:
        return str(self.value)
