"""Unit smoke tests for tests/support (no database required)."""

from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
from hypothesis import settings
from relaypay.idempotency import canonical_json_bytes
from relaypay.provider_operations.validation import (
    ExpectedProviderResult,
    classify_provider_observation,
)

from tests.support.fake_provider import ACCOUNT_ID, SIGNING_SECRET, FakeProvider, Outcome


def _request(stable_key: str, kind: str = "CAPTURE") -> bytes:
    return canonical_json_bytes(
        {
            "accountId": ACCOUNT_ID,
            "amount": 5_000,
            "currency": "INR",
            "operationKind": kind,
            "reference": f"ref_{stable_key}",
            "stableKey": stable_key,
        }
    )


def _expected(stable_key: str, kind: str = "CAPTURE") -> ExpectedProviderResult:
    return ExpectedProviderResult(ACCOUNT_ID, stable_key, kind, f"ref_{stable_key}", 5_000, "INR")


def _classify(provider: FakeProvider, key: str, *, lookup: bool) -> str:
    observation = (
        provider.lookup(account_id=ACCOUNT_ID, stable_key=key)
        if lookup
        else provider.mutate(_request(key))
    )
    return classify_provider_observation(
        observation, expected=_expected(key), signing_secret=SIGNING_SECRET, is_lookup=lookup
    ).classification


def test_replies_pass_real_validation_for_each_scripted_outcome() -> None:
    provider = FakeProvider()
    provider.script("k-decline", Outcome.DECLINE)
    provider.script("k-pending", Outcome.PENDING)
    assert _classify(provider, "k-ok", lookup=False) == "VERIFIED_SUCCESS"
    assert _classify(provider, "k-decline", lookup=False) == "VERIFIED_BUSINESS_DECLINE"
    assert _classify(provider, "k-pending", lookup=False) == "VERIFIED_PENDING"
    assert _classify(provider, "k-ok", lookup=True) == "VERIFIED_SUCCESS"


def test_lose_response_commits_effect_then_raises_and_lookup_succeeds() -> None:
    provider = FakeProvider()
    provider.script("k-lost", Outcome.LOSE_RESPONSE)
    with pytest.raises(httpx.TransportError):
        provider.mutate(_request("k-lost"))
    assert provider.effects["k-lost"].outcome == "SUCCEEDED"
    assert _classify(provider, "k-lost", lookup=True) == "VERIFIED_SUCCESS"
    assert provider.mutation_count("k-lost") == 1
    assert provider.lookup_count("k-lost") == 1


def test_repeat_mutation_is_idempotent_but_counted_and_unknown_lookup_is_404() -> None:
    provider = FakeProvider()
    first = provider.mutate(_request("k-dup"))
    second = provider.mutate(_request("k-dup"))
    assert first.body == second.body
    assert provider.mutation_count("k-dup") == 2
    assert len(provider.effects) == 1
    with pytest.raises(AssertionError, match="more than once"):
        provider.assert_exactly_once()
    assert provider.lookup(account_id=ACCOUNT_ID, stable_key="nope").status_code == 404


def test_concurrent_mutations_create_one_effect_per_key() -> None:
    provider = FakeProvider()
    keys = [f"k-{n % 10}" for n in range(200)]
    with ThreadPoolExecutor(max_workers=16) as pool:
        list(pool.map(lambda key: provider.mutate(_request(key)), keys))
    assert len(provider.effects) == 10
    assert provider.mutation_count() == 200
    assert sum(provider.mutation_count(f"k-{n}") for n in range(10)) == 200


def test_hypothesis_ci_profile_is_loaded_by_default() -> None:
    assert settings().max_examples in {40, 500}
    assert settings().deadline is None
