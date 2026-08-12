from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

FailureClassification = Literal[
    "RETRYABLE_SOFT_DECLINE",
    "NON_RETRYABLE_HARD_DECLINE",
    "AUTHENTICATION_REQUIRED",
    "EXPIRED_METHOD",
    "TRANSPORT_UNKNOWN",
]
Channel = Literal["EMAIL", "WHATSAPP", "IN_APP"]

PROVIDER_CODE_CLASSIFICATION: dict[str, FailureClassification] = {
    "INSUFFICIENT_FUNDS": "RETRYABLE_SOFT_DECLINE",
    "TEMPORARY_DECLINE": "RETRYABLE_SOFT_DECLINE",
    "DO_NOT_HONOUR": "NON_RETRYABLE_HARD_DECLINE",
    "LOST_OR_STOLEN": "NON_RETRYABLE_HARD_DECLINE",
    "AUTHENTICATION_REQUIRED": "AUTHENTICATION_REQUIRED",
    "EXPIRED_METHOD": "EXPIRED_METHOD",
    "TRANSPORT_UNKNOWN": "TRANSPORT_UNKNOWN",
}


@dataclass(frozen=True, slots=True)
class PolicyDefinition:
    max_payment_retries: int = 3
    max_messages: int = 3
    window_days: int = 14
    retry_windows_hours: tuple[int, ...] = (24, 72, 168)
    message_windows_hours: tuple[int, ...] = (0, 48, 120)

    def __post_init__(self) -> None:
        if not 0 <= self.max_payment_retries <= 3:
            raise ValueError("payment retry limit must be between zero and three")
        if not 0 <= self.max_messages <= 3:
            raise ValueError("message limit must be between zero and three")
        if not 1 <= self.window_days <= 14:
            raise ValueError("recovery window must be between one and fourteen days")
        if len(self.retry_windows_hours) < self.max_payment_retries:
            raise ValueError("one retry window is required for every allowed retry")
        if len(self.message_windows_hours) < self.max_messages:
            raise ValueError("one message window is required for every allowed message")
        maximum = self.window_days * 24
        if any(value < 0 or value > maximum for value in self.retry_windows_hours):
            raise ValueError("retry window falls outside the recovery period")
        if any(value < 0 or value > maximum for value in self.message_windows_hours):
            raise ValueError("message window falls outside the recovery period")


@dataclass(frozen=True, slots=True)
class ProposedAction:
    action_type: Literal["PAYMENT_RETRY", "MESSAGE"]
    scheduled_for: datetime
    channel: Channel | None


def classify_provider_code(provider_code: str) -> FailureClassification:
    try:
        return PROVIDER_CODE_CLASSIFICATION[provider_code]
    except KeyError as exc:
        raise ValueError("unsupported recurring payment failure code") from exc


def consented_channels(consent: dict[str, object]) -> tuple[Channel, ...]:
    raw = consent.get("channels", [])
    if not isinstance(raw, list):
        raise ValueError("consent channels must be a list")
    allowed: list[Channel] = []
    for value in raw:
        if value == "EMAIL":
            allowed.append("EMAIL")
        elif value == "WHATSAPP":
            allowed.append("WHATSAPP")
        elif value == "IN_APP":
            allowed.append("IN_APP")
        else:
            raise ValueError("unsupported consent channel")
    return tuple(dict.fromkeys(allowed))


def propose_actions(
    *,
    classification: FailureClassification,
    consent: dict[str, object],
    started_at: datetime,
    policy: PolicyDefinition,
) -> tuple[ProposedAction, ...]:
    if classification in {"NON_RETRYABLE_HARD_DECLINE", "TRANSPORT_UNKNOWN"}:
        return ()
    channels = consented_channels(consent)
    actions: list[ProposedAction] = []
    if classification == "RETRYABLE_SOFT_DECLINE":
        actions.extend(
            ProposedAction("PAYMENT_RETRY", started_at + timedelta(hours=hours), None)
            for hours in policy.retry_windows_hours[: policy.max_payment_retries]
        )
    actions.extend(
        ProposedAction(
            "MESSAGE",
            started_at + timedelta(hours=hours),
            channels[index % len(channels)],
        )
        for index, hours in enumerate(policy.message_windows_hours[: policy.max_messages])
        if channels
    )
    return tuple(sorted(actions, key=lambda item: (item.scheduled_for, item.action_type)))
