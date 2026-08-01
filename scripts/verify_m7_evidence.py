"""Reject absent, invented, or structurally incomplete M7 measurement evidence."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("performance", type=Path)
    parser.add_argument("query_plans", type=Path)
    arguments = parser.parse_args()
    performance = json.loads(arguments.performance.read_text())
    plans = json.loads(arguments.query_plans.read_text())
    measurements = performance["measurements"]
    latency = measurements["latencyMilliseconds"]
    if performance["workload"]["requests"] < 1 or measurements["failures"] != 0:
        raise SystemExit("performance evidence is incomplete")
    if not 0 <= latency["p50"] <= latency["p95"] <= latency["p99"] <= latency["max"]:
        raise SystemExit("latency percentiles are not ordered observations")
    if plans["datasetRows"] != 5000 or not plans["transactionRolledBack"]:
        raise SystemExit("query-plan dataset is not the bounded rollback-only proof")
    for key in ("beforeIndex", "afterIndex"):
        if "Execution Time" not in plans[key] or "Plan" not in plans[key]:
            raise SystemExit(f"{key} is not an analyzed PostgreSQL plan")
    print("M7 measured evidence verified")


if __name__ == "__main__":
    main()
