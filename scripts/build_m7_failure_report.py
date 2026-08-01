"""Convert measured JUnit durations for the six required failure proofs to JSON."""

from __future__ import annotations

import argparse
import json
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path

SCENARIOS = {
    "workerShutdown": "test_recovery_worker_uses_redis_as_broker_only_and_late_acknowledgement",
    "commitThenCrash": (
        "test_crash_after_lookup_response_persistence_recovers_without_mutation_retry"
    ),
    "redisUnavailable": "test_redis_loss_keeps_postgresql_poller_available",
    "slowProvider": "test_slow_provider_fault_is_bounded_and_commits_effect_before_response",
    "retryExhaustion": "test_retry_budget_ends_in_dead_letter",
    "queueNotificationLoss": (
        "test_queue_notification_loss_is_repaired_by_periodic_authoritative_scans"
    ),
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("junit", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    root = ET.parse(arguments.junit).getroot()  # noqa: S314 - locally generated JUnit only
    cases = {case.attrib["name"]: case for case in root.iter("testcase")}
    results: dict[str, object] = {}
    for scenario, test_name in SCENARIOS.items():
        case = cases.get(test_name)
        if case is None or case.find("failure") is not None or case.find("error") is not None:
            raise SystemExit(f"missing passing failure proof: {scenario}")
        results[scenario] = {
            "test": test_name,
            "outcome": "passed",
            "measuredSeconds": float(case.attrib.get("time", "0")),
        }
    payload = {
        "schemaVersion": 1,
        "recordedAt": datetime.now(UTC).isoformat(),
        "scenarios": results,
        "claim": "Measured bounded proof behavior only; no production availability claim.",
    }
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(f"{json.dumps(payload, indent=2, sort_keys=True)}\n")


if __name__ == "__main__":
    main()
