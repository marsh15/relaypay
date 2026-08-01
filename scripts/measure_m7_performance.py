"""Measure a bounded real HTTP workload; this is evidence, not an SLO claim."""

from __future__ import annotations

import argparse
import json
import statistics
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path


def _percentile(samples: list[float], percentile: float) -> float:
    ordered = sorted(samples)
    index = max(0, min(len(ordered) - 1, int((len(ordered) - 1) * percentile)))
    return ordered[index]


def measure(base_url: str, *, requests: int, concurrency: int) -> dict[str, object]:
    if not base_url.startswith(("http://", "https://")):
        raise ValueError("base URL must use HTTP or HTTPS")
    endpoint = f"{base_url.rstrip('/')}/health/live"

    def one(_: int) -> float:
        started = time.perf_counter()
        with urllib.request.urlopen(endpoint, timeout=5) as response:  # noqa: S310
            if response.status != 200 or response.read() != b'{"status":"live"}':
                raise RuntimeError("health probe returned an unexpected response")
        return (time.perf_counter() - started) * 1_000

    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        samples = list(pool.map(one, range(requests)))
    elapsed = time.perf_counter() - started
    return {
        "schemaVersion": 1,
        "recordedAt": datetime.now(UTC).isoformat(),
        "workload": {
            "endpoint": "/health/live",
            "requests": requests,
            "concurrency": concurrency,
            "boundedDataset": True,
        },
        "measurements": {
            "elapsedSeconds": round(elapsed, 6),
            "throughputRequestsPerSecond": round(requests / elapsed, 3),
            "latencyMilliseconds": {
                "min": round(min(samples), 3),
                "mean": round(statistics.fmean(samples), 3),
                "p50": round(_percentile(samples, 0.50), 3),
                "p95": round(_percentile(samples, 0.95), 3),
                "p99": round(_percentile(samples, 0.99), 3),
                "max": round(max(samples), 3),
            },
            "failures": 0,
        },
        "claim": "Observed CI/local proof values only; no production SLO is defined.",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--requests", type=int, default=200)
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    if not 1 <= arguments.requests <= 10_000:
        raise SystemExit("--requests must be between 1 and 10000")
    if not 1 <= arguments.concurrency <= 100:
        raise SystemExit("--concurrency must be between 1 and 100")
    payload = json.dumps(
        measure(
            arguments.base_url,
            requests=arguments.requests,
            concurrency=arguments.concurrency,
        ),
        indent=2,
        sort_keys=True,
    )
    if arguments.output:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(f"{payload}\n")
    else:
        print(payload)


if __name__ == "__main__":
    main()
