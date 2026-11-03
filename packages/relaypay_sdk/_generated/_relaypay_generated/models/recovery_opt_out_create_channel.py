from enum import StrEnum

class RecoveryOptOutCreateChannel(StrEnum):
    ALL = "ALL"
    EMAIL = "EMAIL"
    IN_APP = "IN_APP"
    WHATSAPP = "WHATSAPP"

    def __str__(self) -> str:
        return str(self.value)
