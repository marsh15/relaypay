"""Run a measured synthetic k6 workload and retain summaries even if thresholds fail."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from scripts.loadtest_seed import seed_load_tenant, write_credentials
from scripts.machine_info import collect_machine_info

WORKLOADS = ("create_intent", "authorize", "capture", "refund", "mixed")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--script", choices=WORKLOADS, default="mixed")
    parser.add_argument("--vus", type=int, default=4)
    parser.add_argument("--duration", default="30s")
    parser.add_argument("--limiter", choices=("on", "off"), default="off")
    args = parser.parse_args()
    if not 1 <= args.vus <= 500:
        parser.error("--vus must be between 1 and 500")
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    directory = Path("output/load", run_id).resolve()
    directory.mkdir(parents=True)
    info = collect_machine_info()
    info["command"] = {
        "script": args.script,
        "vus": args.vus,
        "duration": args.duration,
        "limiterLabel": args.limiter,
    }
    (directory / "machine.json").write_text(json.dumps(info, indent=2, sort_keys=True) + "\n")
    with tempfile.TemporaryDirectory(prefix="relaypay-load-") as temp:
        credentials = Path(temp, "credentials.json")
        tenant = seed_load_tenant()
        write_credentials(credentials, tenant)
        (directory / "tenant.json").write_text(
            json.dumps({key: value for key, value in tenant.items() if key != "apiKey"}, indent=2)
            + "\n"
        )
        environment = {
            **os.environ,
            "LOAD_RUN_ID": run_id,
            "LOAD_SCRIPT": args.script,
            "LOAD_RESULTS_DIR": str(directory),
            "LOAD_CREDENTIALS_FILE": str(credentials),
            "LOAD_UID": str(os.getuid()),
            "LOAD_GID": str(os.getgid()),
            "LOAD_VUS": str(args.vus),
            "LOAD_DURATION": args.duration,
            "LOAD_LIMITER": args.limiter,
        }
        command = [
            "docker",
            "compose",
            "-f",
            "compose.yaml",
            "-f",
            "compose.load.yaml",
            "--profile",
            "load",
            "run",
            "--rm",
            "--no-deps",
            "k6",
        ]
        result = subprocess.run(command, env=environment, check=False)  # noqa: S603
    (directory / "exit.json").write_text(json.dumps({"exitCode": result.returncode}) + "\n")
    print(f"Load artifacts: {directory}")
    raise SystemExit(result.returncode)


if __name__ == "__main__":
    main()
