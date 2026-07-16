from relaypay.contracts import EmptyCommand, RefundCreate
from relaypay.idempotency import build_fingerprint, canonical_json_bytes, digest_secret


def test_canonical_json_has_stable_sorted_representation() -> None:
    assert canonical_json_bytes({"b": 2, "a": {"z": 1, "x": 0}}) == (b'{"a":{"x":0,"z":1},"b":2}')


def test_fingerprint_binds_route_path_and_validated_body() -> None:
    capture = build_fingerprint(
        api_version="v1",
        method="POST",
        route_template="/payment_intents/{payment_intent_id}/capture",
        path_params={"payment_intent_id": "pay_" + "1" * 32},
        body=EmptyCommand(),
    )
    refund = build_fingerprint(
        api_version="v1",
        method="POST",
        route_template="/payment_intents/{payment_intent_id}/refunds",
        path_params={"payment_intent_id": "pay_" + "1" * 32},
        body=RefundCreate(amount=500, currency="INR"),
    )

    assert capture.sha256 != refund.sha256
    assert capture.safe_summary["route_template"].endswith("/capture")


def test_secret_digest_is_peppered_and_stable() -> None:
    assert digest_secret("key", "pepper-a") == digest_secret("key", "pepper-a")
    assert digest_secret("key", "pepper-a") != digest_secret("key", "pepper-b")
