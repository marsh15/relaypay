"""Release-only live-provider comparison for the settlement question classifier.

Replays the versioned settlement evaluation questions through OpenAI, Claude,
and Gemini structured endpoints when the matching API keys are configured
(OPENAI_API_KEY, ANTHROPIC_API_KEY, GEMINI_API_KEY). Never invoked by CI; the
deterministic evaluation runner is the release gate, this report only compares
live model behaviour against the same fixtures.

Questions are PII-tokenized before leaving the process (all fixture data is
synthetic regardless). Writes output/v1/live-provider-comparison.json.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import httpx
from relaypay.agent_runtime.contracts import (
    ModelRequest,
    RetryableProviderError,
    TerminalModelError,
)
from relaypay.agent_runtime.providers import JsonHttpProvider
from relaypay.agent_runtime.security import tokenize_pii
from relaypay.settlement_intelligence.classification import (
    QuestionClassification,
    classification_prompt,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "evaluations" / "v1" / "settlement-v1.json"
OUTPUT = ROOT / "output" / "v1" / "live-provider-comparison.json"
TIMEOUT_SECONDS = 30.0

# List prices at time of writing, micro-USD per token; verify before quoting cost.
PRICE_USD_MICROS_PER_TOKEN: dict[str, tuple[int, int]] = {
    "openai": (400, 1600),  # gpt-4.1-mini input/output
    "claude": (80, 400),  # claude-haiku input/output
    "gemini": (100, 400),  # gemini-2.0-flash input/output
}


@dataclass(frozen=True, slots=True)
class QuestionOutcome:
    provider: str
    case_id: str
    expected_intent: str
    observed_intent: str | None
    schema_failure: bool
    fallback_used: bool
    latency_ms: int
    input_tokens: int
    output_tokens: int


def _openai_transport(api_key: str) -> Callable[[ModelRequest], dict[str, object]]:
    def transport(request: ModelRequest) -> dict[str, object]:
        response = httpx.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": "gpt-4.1-mini",
                "messages": [{"role": "user", "content": request.prompt}],
                "response_format": {"type": "json_object"},
            },
            timeout=TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        body = response.json()
        return {
            "output": json.loads(body["choices"][0]["message"]["content"]),
            "inputTokens": body["usage"]["prompt_tokens"],
            "outputTokens": body["usage"]["completion_tokens"],
            "finishStatus": str(body["choices"][0]["finish_reason"]),
        }

    return transport


def _claude_transport(api_key: str) -> Callable[[ModelRequest], dict[str, object]]:
    def transport(request: ModelRequest) -> dict[str, object]:
        response = httpx.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": api_key, "anthropic-version": "2023-06-01"},
            json={
                "model": "claude-haiku",
                "max_tokens": 512,
                "messages": [{"role": "user", "content": request.prompt}],
            },
            timeout=TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        body = response.json()
        return {
            "output": json.loads(body["content"][0]["text"]),
            "inputTokens": body["usage"]["input_tokens"],
            "outputTokens": body["usage"]["output_tokens"],
            "finishStatus": str(body["stop_reason"]),
        }

    return transport


def _gemini_transport(api_key: str) -> Callable[[ModelRequest], dict[str, object]]:
    def transport(request: ModelRequest) -> dict[str, object]:
        response = httpx.post(
            "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent",
            params={"key": api_key},
            json={
                "contents": [{"parts": [{"text": request.prompt}]}],
                "generationConfig": {"responseMimeType": "application/json"},
            },
            timeout=TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        body = response.json()
        usage = body.get("usageMetadata", {})
        return {
            "output": json.loads(body["candidates"][0]["content"]["parts"][0]["text"]),
            "inputTokens": int(usage.get("promptTokenCount", 0)),
            "outputTokens": int(usage.get("candidatesTokenCount", 0)),
            "finishStatus": str(body["candidates"][0].get("finishReason", "STOP")),
        }

    return transport


def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, round(fraction * (len(ordered) - 1)))
    return ordered[index]


def aggregate(
    outcomes: list[QuestionOutcome],
    providers: list[str],
    dataset_sha256: str,
) -> dict[str, object]:
    provider_rows: list[dict[str, object]] = []
    report: dict[str, object] = {
        "dataset": "settlement-v1",
        "datasetSha256": dataset_sha256,
        "providers": provider_rows,
    }
    for provider in providers:
        rows = [row for row in outcomes if row.provider == provider]
        price_in, price_out = PRICE_USD_MICROS_PER_TOKEN.get(provider, (0, 0))
        latencies = [float(row.latency_ms) for row in rows if not row.schema_failure]
        provider_rows.append(
            {
                "provider": provider,
                "questions": len(rows),
                "schemaFailures": sum(1 for row in rows if row.schema_failure),
                "intentMismatches": sum(
                    1
                    for row in rows
                    if not row.schema_failure and row.observed_intent != row.expected_intent
                ),
                "fallbackUsed": sum(1 for row in rows if row.fallback_used),
                "latencyP50Ms": _percentile(latencies, 0.50),
                "latencyP95Ms": _percentile(latencies, 0.95),
                "inputTokens": sum(row.input_tokens for row in rows),
                "outputTokens": sum(row.output_tokens for row in rows),
                "costUsdMicros": sum(
                    row.input_tokens * price_in + row.output_tokens * price_out for row in rows
                ),
            }
        )
    return report


def run_comparison(
    providers: list[tuple[str, JsonHttpProvider]],
    cases: list[dict[str, object]],
    dataset_sha256: str,
) -> dict[str, object]:
    """Ask every fixture question of the first configured provider, falling
    back down the mandated OpenAI -> Claude -> Gemini order on retryable
    failures, and record one outcome row per provider actually contacted."""
    outcomes: list[QuestionOutcome] = []
    for case in cases:
        assert isinstance(case, dict)
        question = tokenize_pii(str(case["question"])).text
        expected_intent = str(case["intent"])
        request = ModelRequest(
            prompt=classification_prompt(question),
            schema=QuestionClassification,
            model_id="live-comparison-v1",
            max_output_tokens=512,
            trace_id=str(case["caseId"]),
        )
        for index, (name, provider) in enumerate(providers):
            started = time.monotonic_ns()
            try:
                result = provider.generate_structured(request)
            except (RetryableProviderError, TerminalModelError):
                if index == len(providers) - 1:
                    outcomes.append(
                        QuestionOutcome(
                            provider=name,
                            case_id=str(case["caseId"]),
                            expected_intent=expected_intent,
                            observed_intent=None,
                            schema_failure=True,
                            fallback_used=index > 0,
                            latency_ms=(time.monotonic_ns() - started) // 1_000_000,
                            input_tokens=0,
                            output_tokens=0,
                        )
                    )
                continue
            assert isinstance(result.output, QuestionClassification)
            outcomes.append(
                QuestionOutcome(
                    provider=name,
                    case_id=str(case["caseId"]),
                    expected_intent=expected_intent,
                    observed_intent=result.output.intent,
                    schema_failure=False,
                    fallback_used=index > 0,
                    latency_ms=(time.monotonic_ns() - started) // 1_000_000,
                    input_tokens=result.input_tokens,
                    output_tokens=result.output_tokens,
                )
            )
            break
    return aggregate(outcomes, [name for name, _ in providers], dataset_sha256)


def main() -> None:
    configured: list[tuple[str, JsonHttpProvider]] = []
    for name, env_key, transport in (
        ("openai", "OPENAI_API_KEY", _openai_transport),
        ("claude", "ANTHROPIC_API_KEY", _claude_transport),
        ("gemini", "GEMINI_API_KEY", _gemini_transport),
    ):
        api_key = os.environ.get(env_key)
        if api_key:
            configured.append((name, JsonHttpProvider(name=name, transport=transport(api_key))))
    if not configured:
        print("No live provider keys configured; comparison skipped (this is not a CI gate).")
        return
    document = json.loads(FIXTURES.read_text())
    cases = document["cases"]
    assert isinstance(cases, list)
    report = run_comparison(
        configured,
        cases,
        dataset_sha256=hashlib.sha256(FIXTURES.read_bytes()).hexdigest(),
    )
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(f"wrote {OUTPUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
