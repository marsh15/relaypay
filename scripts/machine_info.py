"""Record reproducibility context without database credentials or API keys."""

from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from relaypay.config import get_settings
from relaypay.database import build_engine
from sqlalchemy import text


def _command(*arguments: str) -> str:
    result = subprocess.run(arguments, capture_output=True, text=True, check=True)  # noqa: S603
    return result.stdout.strip()


def memory_bytes() -> int | None:
    if platform.system() == "Darwin":
        return int(_command("/usr/sbin/sysctl", "-n", "hw.memsize"))
    if hasattr(os, "sysconf"):
        try:
            return int(os.sysconf("SC_PAGE_SIZE")) * int(os.sysconf("SC_PHYS_PAGES"))
        except (ValueError, OSError):
            return None
    return None


def cpu_model() -> str:
    if platform.system() == "Darwin":
        return _command("/usr/sbin/sysctl", "-n", "machdep.cpu.brand_string")
    cpuinfo = Path("/proc/cpuinfo")
    if cpuinfo.is_file():
        for line in cpuinfo.read_text().splitlines():
            if line.startswith("model name"):
                return line.partition(":")[2].strip()
    return platform.processor()


def collect_machine_info() -> dict[str, Any]:
    settings = get_settings()
    engine = build_engine(
        settings.RELAYPAY_DATABASE_URL.get_secret_value(), application_name="relaypay-load-info"
    )
    try:
        with engine.connect() as connection:
            database = dict(
                connection.execute(
                    text(
                        "SELECT version() AS version, "
                        "current_setting('max_connections') AS max_connections, "
                        "current_setting('shared_buffers') AS shared_buffers, "
                        "current_setting('work_mem') AS work_mem"
                    )
                )
                .mappings()
                .one()
            )
            counts = dict(
                connection.execute(
                    text(
                        "SELECT (SELECT count(*) FROM payment_intents) AS payments, "
                        "(SELECT count(*) FROM postings) AS postings, "
                        "(SELECT count(*) FROM provider_operations) AS operations"
                    )
                )
                .mappings()
                .one()
            )
        return {
            "recordedAt": datetime.now(UTC).isoformat(),
            "gitCommit": _command("git", "rev-parse", "HEAD"),
            "gitDirty": bool(_command("git", "status", "--porcelain")),
            "host": {
                "os": platform.platform(),
                "machine": platform.machine(),
                "processor": cpu_model(),
                "logicalCpus": os.cpu_count(),
                "memoryBytes": memory_bytes(),
            },
            "database": database,
            "datasetBeforeRun": counts,
            "application": {
                "sqlalchemyPoolSizePerEngine": 5,
                "sqlalchemyMaxOverflowPerEngine": 10,
                "apiWorkers": 1,
                "pollerProcesses": 1,
                "topology": "compose.yaml defaults; record any overrides separately",
            },
        }
    finally:
        engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    payload = collect_machine_info()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
