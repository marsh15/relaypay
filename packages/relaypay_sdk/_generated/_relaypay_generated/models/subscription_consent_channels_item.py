from enum import Enum

class SubscriptionConsentChannelsItem(str, Enum):
    EMAIL = "EMAIL"
    IN_APP = "IN_APP"
    WHATSAPP = "WHATSAPP"

    def __str__(self) -> str:
        return str(self.value)
