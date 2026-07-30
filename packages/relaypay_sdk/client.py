import secrets
from collections.abc import Iterator
from dataclasses import dataclass
from types import TracebackType
from typing import Any, cast

import httpx

from relaypay_sdk.errors import RelayPayAPIError


@dataclass(frozen=True, slots=True)
class APIKey:
    value: str

    def __post_init__(self) -> None:
        if (
            not (self.value.startswith("rpk_test_") or self.value.startswith("rpk_live_like_"))
            or "." not in self.value
        ):
            raise ValueError("RelayPay API key has an invalid public prefix")

    @property
    def environment(self) -> str:
        return "TEST" if self.value.startswith("rpk_test_") else "LIVE_LIKE"


@dataclass(frozen=True, slots=True)
class IdempotencyKey:
    value: str

    def __post_init__(self) -> None:
        if not self.value.strip() or len(self.value) > 255:
            raise ValueError("Idempotency key must contain 1-255 characters")

    @classmethod
    def new(cls) -> "IdempotencyKey":
        return cls(f"rpi_{secrets.token_urlsafe(24)}")


@dataclass(frozen=True, slots=True)
class PaymentIntent:
    id: str
    merchant_reference: str
    amount: int
    currency: str
    authorization_status: str | None
    capture_status: str | None
    created_at: str

    @classmethod
    def from_document(cls, document: dict[str, Any]) -> "PaymentIntent":
        return cls(
            id=str(document["id"]),
            merchant_reference=str(document["merchantReference"]),
            amount=int(document["amount"]),
            currency=str(document["currency"]),
            authorization_status=document.get("authorizationStatus"),
            capture_status=document.get("captureStatus"),
            created_at=str(document["createdAt"]),
        )


@dataclass(frozen=True, slots=True)
class PaymentIntentPage:
    data: list[PaymentIntent]
    next_cursor: str | None


class RelayPayClient:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: APIKey | str,
        timeout_seconds: float = 10.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        resolved_key = api_key if isinstance(api_key, APIKey) else APIKey(api_key)
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {resolved_key.value}"},
            timeout=timeout_seconds,
            transport=transport,
        )

    def __enter__(self) -> "RelayPayClient":
        self._client.__enter__()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self._client.__exit__(exc_type, exc_value, traceback)

    def close(self) -> None:
        self._client.close()

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        response = self._client.request(method, path, **kwargs)
        if response.is_success:
            return response
        try:
            error = response.json()["error"]
            code = str(error["code"])
            message = str(error["message"])
            details = error.get("details") or {}
        except (ValueError, KeyError, TypeError):
            code = "UNEXPECTED_RESPONSE"
            message = "RelayPay returned an undocumented error response"
            details = {}
        retry_after = response.headers.get("Retry-After")
        raise RelayPayAPIError(
            status_code=response.status_code,
            code=code,
            message=message,
            details=details,
            request_id=response.headers.get("X-Request-ID"),
            retry_after=int(retry_after) if retry_after and retry_after.isdigit() else None,
        )

    def list_payment_intents(
        self,
        *,
        limit: int = 25,
        after: str | None = None,
        merchant_reference: str | None = None,
    ) -> PaymentIntentPage:
        params: dict[str, str | int] = {"limit": limit}
        if after is not None:
            params["after"] = after
        if merchant_reference is not None:
            params["merchantReference"] = merchant_reference
        document = self._request("GET", "/api/v1/payment_intents", params=params).json()
        return PaymentIntentPage(
            data=[PaymentIntent.from_document(item) for item in document["data"]],
            next_cursor=document["nextCursor"],
        )

    def create_payment_intent(
        self,
        *,
        customer_id: str,
        merchant_reference: str,
        amount: int,
        idempotency_key: IdempotencyKey,
    ) -> dict[str, Any]:
        return cast(
            dict[str, Any],
            self._request(
                "POST",
                "/api/v1/payment_intents",
                headers={"Idempotency-Key": idempotency_key.value},
                json={
                    "customer_id": customer_id,
                    "merchant_reference": merchant_reference,
                    "amount": amount,
                    "currency": "INR",
                },
            ).json(),
        )

    def iter_payment_intents(
        self,
        *,
        page_size: int = 25,
        merchant_reference: str | None = None,
    ) -> Iterator[PaymentIntent]:
        cursor: str | None = None
        while True:
            page = self.list_payment_intents(
                limit=page_size,
                after=cursor,
                merchant_reference=merchant_reference,
            )
            yield from page.data
            cursor = page.next_cursor
            if cursor is None:
                return
