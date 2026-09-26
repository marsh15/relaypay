from relaypay.agent_runtime.contracts import ModelRequest, RetryableProviderError
from relaypay.agent_runtime.providers import JsonHttpProvider

from scripts.compare_live_providers import (
    QuestionOutcome,
    aggregate,
    run_comparison,
)


def _ok_transport(intent: str):
    def transport(request: ModelRequest) -> dict[str, object]:
        del request
        return {
            "output": {"intent": intent, "dates": [], "reason": "synthetic"},
            "inputTokens": 100,
            "outputTokens": 10,
            "finishStatus": "STOP",
        }

    return transport


def _outage_transport(request: ModelRequest) -> dict[str, object]:
    del request
    raise RetryableProviderError("outage")


def _intent_switching_transport():
    def transport(request: ModelRequest) -> dict[str, object]:
        if "cash impact" in request.prompt.casefold():
            return _ok_transport("REFUND_IMPACT")(request)
        return _ok_transport("SETTLEMENT_LOWER")(request)

    return transport


def test_run_comparison_records_fallback_and_intent_agreement() -> None:
    cases = [
        {
            "caseId": "q1",
            "question": "Why is today's settlement lower than the forecast?",
            "intent": "SETTLEMENT_LOWER",
        },
        {
            "caseId": "q2",
            "question": "What is the refund cash impact today?",
            "intent": "REFUND_IMPACT",
        },
    ]
    providers = [
        ("openai", JsonHttpProvider(name="openai", transport=_outage_transport)),
        ("claude", JsonHttpProvider(name="claude", transport=_intent_switching_transport())),
    ]
    report = run_comparison(providers, cases, dataset_sha256="abc")
    rows = {entry["provider"]: entry for entry in report["providers"]}
    assert rows["openai"]["questions"] == 0
    assert rows["claude"]["questions"] == 2
    assert rows["claude"]["fallbackUsed"] == 2
    assert rows["claude"]["intentMismatches"] == 0
    assert rows["claude"]["inputTokens"] == 200
    assert report["datasetSha256"] == "abc"


def test_schema_failure_on_last_provider_is_recorded() -> None:
    def bad_transport(request: ModelRequest) -> dict[str, object]:
        del request
        return {
            "output": {"unexpected": "shape"},
            "inputTokens": 5,
            "outputTokens": 5,
            "finishStatus": "STOP",
        }

    cases = [{"caseId": "q1", "question": "anything", "intent": "SETTLEMENT_LOWER"}]
    providers = [("gemini", JsonHttpProvider(name="gemini", transport=bad_transport))]
    report = run_comparison(providers, cases, dataset_sha256="abc")
    rows = {entry["provider"]: entry for entry in report["providers"]}
    assert rows["gemini"]["schemaFailures"] == 1
    assert rows["gemini"]["questions"] == 1
    assert rows["gemini"]["latencyP50Ms"] is None


def test_aggregate_cost_and_percentiles() -> None:
    outcomes = [
        QuestionOutcome(
            "openai", "q1", "SETTLEMENT_LOWER", "SETTLEMENT_LOWER", False, False, 100, 1_000, 100
        ),
        QuestionOutcome(
            "openai", "q2", "REFUND_IMPACT", "CLARIFICATION", False, False, 300, 2_000, 200
        ),
    ]
    report = aggregate(outcomes, ["openai"], "sha")
    entry = report["providers"][0]
    assert entry["questions"] == 2
    assert entry["intentMismatches"] == 1
    assert entry["fallbackUsed"] == 0
    assert entry["latencyP50Ms"] == 100.0
    assert entry["latencyP95Ms"] == 300.0
    # (1000*400 + 100*1600) + (2000*400 + 200*1600) micro-USD
    assert entry["costUsdMicros"] == (400_000 + 160_000) + (800_000 + 320_000)
