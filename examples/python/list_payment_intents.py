import os

from relaypay_sdk import APIKey, RelayPayClient


def main() -> None:
    key = APIKey(os.environ["RELAYPAY_TEST_API_KEY"])
    if key.environment != "TEST":
        raise SystemExit("This example accepts TEST keys only")
    with RelayPayClient(
        base_url=os.environ.get("RELAYPAY_BASE_URL", "http://localhost:8080"),
        api_key=key,
    ) as client:
        for payment in client.iter_payment_intents(page_size=25):
            print(payment.id, payment.merchant_reference, payment.amount, payment.currency)


if __name__ == "__main__":
    main()
