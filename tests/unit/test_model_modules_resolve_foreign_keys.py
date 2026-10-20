"""Processes must be able to resolve every foreign key their models declare.

``Capture`` gained a foreign key to ``fee_schedule_versions`` while ``payments.models`` did not
import ``fees.models``. The full suite hid it, because other tests imported the fees module
first. CI caught it when ``test_m13_settlement_intelligence.py`` ran alone and every capture
flush raised ``NoReferencedTableError``. Each check runs in a fresh interpreter, so test
ordering cannot mask a missing import. Many service modules do not resolve the whole graph on
their own, and never have; the contract that matters is the process entry points and the model
aggregator, plus the regression itself.
"""

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

PROBE = """
import importlib, sys
from relaypay.database import Base
importlib.import_module(sys.argv[1])
unresolved = []
for table in Base.metadata.tables.values():
    for fk in table.foreign_keys:
        try:
            fk.column
        except Exception as error:  # noqa: BLE001 - report every unresolved target
            unresolved.append(f"{table.name}.{fk.parent.name} -> {fk.target_fullname}: {error}")
if unresolved:
    raise SystemExit("\\n".join(sorted(unresolved)))
"""


def _probe(code: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - fixed interpreter and repository-owned code
        [sys.executable, "-c", code, *args],
        capture_output=True,
        text=True,
        cwd=ROOT,
        check=False,
    )


@pytest.mark.parametrize("module", ["apps.api.main", "apps.worker.poller", "relaypay.models"])
def test_entry_point_resolves_every_foreign_key(module: str) -> None:
    result = _probe(PROBE, module)
    assert result.returncode == 0, result.stderr or result.stdout


def test_payment_models_register_the_fee_schedule_table() -> None:
    result = _probe(
        "import relaypay.payments.models\n"
        "from relaypay.database import Base\n"
        "assert 'fee_schedule_versions' in Base.metadata.tables\n"
    )
    assert result.returncode == 0, result.stderr or result.stdout
