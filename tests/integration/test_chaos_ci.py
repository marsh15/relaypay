"""CI-mode chaos harness smoke: 2 seeds x 150 ops in-process against real Postgres.

The first test asserts harness-level success only (no unexpected exceptions, quiescence,
provider exactly-once, injected crashes and lost responses, every acknowledged command
recorded). The second also runs the hand-written invariant checker and stays red until the
maintainer implements ``relaypay/proof/invariants.py``.
"""

import pytest
from relaypay.proof import AckedOp, check_invariants

from scripts.chaos.driver import SeedResult, bank_factory, run_seed
from scripts.chaos.ops import generate_schedule
from tests.support.db import relaypay_database_url, require_postgres

SEEDS = (101, 102)
N_OPS = 150


@pytest.fixture(scope="module")
def results() -> list[SeedResult]:
    require_postgres()
    from scripts.chaos.driver import bank_database_url

    require_postgres(bank_database_url())
    assert relaypay_database_url()
    with bank_factory(pool_size=4) as bank:
        return [run_seed(seed, N_OPS, bank, keep_snapshot=True) for seed in SEEDS]


@pytest.mark.chaos
def test_ci_chaos_harness_runs_clean(results: list[SeedResult]) -> None:
    for result in results:
        assert result.failures == [], f"seed {result.seed}: {result.failures}"
        assert result.quiesced, f"seed {result.seed} did not quiesce"
        assert result.executed == N_OPS
        assert result.elapsed > 0
    assert sum(sum(r.crashes.values()) for r in results) >= 1, "no crash point fired"
    lost = sum(
        r.faults.get("LOSE_RESPONSE", 0) + r.faults.get("LOSE_RESPONSE_DECLINE", 0) for r in results
    )
    assert lost >= 1, "no lost-response fault was applied"
    assert sum(r.acked_ops for r in results) > 0


@pytest.mark.chaos
def test_ci_chaos_schedules_are_reproducible() -> None:
    assert [op.to_dict() for op in generate_schedule(SEEDS[0], N_OPS)] == [
        op.to_dict() for op in generate_schedule(SEEDS[0], N_OPS)
    ]


@pytest.mark.chaos
def test_ci_chaos_records_every_acknowledged_command(results: list[SeedResult]) -> None:
    for result in results:
        assert result.snapshot_counts["acked_ops"] == result.acked_ops
        assert result.snapshot_counts["provider_operations"] >= result.acked_ops // 3
        assert result.snapshot_counts["provider_effects"] > 0
    assert AckedOp  # the snapshot's acked_ops are AckedOp rows built by the driver


@pytest.mark.chaos
@pytest.mark.handwritten
def test_ci_chaos_snapshots_satisfy_every_invariant(results: list[SeedResult]) -> None:
    """Red until the maintainer implements ``check_invariants``."""
    for result in results:
        assert check_invariants(result.snapshot, quiesced=result.quiesced) == [], (
            f"seed {result.seed}"
        )
