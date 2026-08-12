from enum import Enum

class RecoveryOptOutCreateChannel(str, Enum):
    ALL = "ALL"
    EMAIL = "EMAIL"
    IN_APP = "IN_APP"
    WHATSAPP = "WHATSAPP"

    def __str__(self) -> str:
        return str(self.value)
