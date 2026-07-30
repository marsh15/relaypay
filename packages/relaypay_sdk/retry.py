from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RetryGuidance:
    retryable: bool
    reason: str
    minimum_delay_seconds: int | None = None


def retry_guidance(
    *,
    method: str,
    status_code: int | None,
    idempotency_key: str | None = None,
    retry_after: int | None = None,
) -> RetryGuidance:
    normalized_method = method.upper()
    safe_request = normalized_method in {"GET", "HEAD", "OPTIONS"} or bool(idempotency_key)
    if not safe_request:
        return RetryGuidance(False, "Mutation retries require the original Idempotency-Key")
    if status_code in {429, 502, 503, 504}:
        return RetryGuidance(
            True,
            "Retry with exponential backoff and reuse the original Idempotency-Key",
            retry_after,
        )
    return RetryGuidance(False, "The response is not classified as transient")
