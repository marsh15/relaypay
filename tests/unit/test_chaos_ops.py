"""Chaos schedule generation and the full-mode process-fault plan are pure and seeded."""

import json
from collections import Counter

from scripts.chaos.faults import ALL_CRASH_POINTS
from scripts.chaos.full import compose, format_plan, plan_process_faults
from scripts.chaos.ops import (
    KIND_WEIGHTS,
    Op,
    OpKind,
    generate_schedule,
    schedule_from_json,
    schedule_stats,
    schedule_to_json,
)

N = 3000


def test_same_seed_gives_identical_json_and_different_seeds_differ() -> None:
    first = schedule_to_json(generate_schedule(11, 500))
    assert first == schedule_to_json(generate_schedule(11, 500))
    assert first != schedule_to_json(generate_schedule(12, 500))


def test_schedule_is_a_prefix_stable_and_round_trips_through_json() -> None:
    long = generate_schedule(5, 400)
    assert [op.to_dict() for op in generate_schedule(5, 100)] == [op.to_dict() for op in long[:100]]
    assert schedule_from_json(schedule_to_json(long)) == long
    json.loads(schedule_to_json(long))  # plain JSON


def test_indices_are_sequential_and_start_with_a_create() -> None:
    ops = generate_schedule(3, 200)
    assert [op.index for op in ops] == list(range(200))
    assert ops[0].kind == OpKind.CREATE


def test_kind_mix_tracks_the_weights() -> None:
    counts = Counter(op.kind for op in generate_schedule(1, N))
    assert set(counts) == {kind.value for kind, _ in KIND_WEIGHTS}
    total_weight = sum(weight for _, weight in KIND_WEIGHTS)
    for kind, weight in KIND_WEIGHTS:
        expected = N * weight / total_weight
        # CREATE also absorbs the no-payment-yet and nothing-to-replay fallbacks.
        tolerance = 0.45 if kind in {OpKind.CREATE, OpKind.CONCURRENT_DUPLICATE} else 0.35
        assert abs(counts[kind.value] - expected) <= expected * tolerance, kind


def test_references_point_at_earlier_operations_of_the_right_kind() -> None:
    ops = generate_schedule(9, 1000)
    by_index = {op.index: op for op in ops}
    for op in ops:
        if op.target is not None:
            assert by_index[op.target].kind == OpKind.CREATE and op.target < op.index
        if op.ref is not None:
            assert op.ref < op.index
            assert by_index[op.ref].kind in {
                OpKind.CREATE,
                OpKind.AUTHORIZE,
                OpKind.CAPTURE,
                OpKind.REFUND,
                OpKind.PAYOUT,
            }


def test_faults_crashes_and_invalid_commands_are_present_but_a_minority() -> None:
    ops = generate_schedule(2, N)
    stats = schedule_stats(ops)
    assert 0 < sum(stats["faults_planned"].values()) < N * 0.25
    assert 0 < sum(stats["crashes_planned"].values()) < N * 0.2
    assert set(stats["crashes_planned"]) <= set(ALL_CRASH_POINTS)
    assert set(stats["faults_planned"]) <= {
        "LOSE_RESPONSE",
        "PENDING",
        "DECLINE",
        "LOSE_RESPONSE_DECLINE",
    }
    assert 0 < stats["invalid"] < N * 0.2


def test_concurrent_duplicates_use_two_to_four_threads_and_a_command() -> None:
    duplicates = [op for op in generate_schedule(4, N) if op.kind == OpKind.CONCURRENT_DUPLICATE]
    assert duplicates
    assert {op.threads for op in duplicates} <= {2, 3, 4}
    assert {op.command for op in duplicates} <= {"AUTHORIZE", "CAPTURE", "REFUND"}


def test_refunds_are_partial_and_sometimes_too_large() -> None:
    ops = generate_schedule(6, N)
    amounts = {op.index: op.amount for op in ops if op.kind == OpKind.CREATE}
    refunds = [op for op in ops if op.kind == OpKind.REFUND and op.target is not None]
    assert refunds
    assert any(
        op.amount is not None and op.amount > (amounts[op.target] or 0) for op in refunds
    ) or any(op.invalid for op in refunds)
    assert any(op.amount is not None and op.amount < (amounts[op.target] or 0) for op in refunds)


def test_op_dict_omits_defaults_but_keeps_zero_values() -> None:
    assert Op(index=1, kind="SETTLE").to_dict() == {"index": 1, "kind": "SETTLE"}
    assert Op(index=0, kind="RECOVER_TICK", pick=0).to_dict() == {
        "index": 0,
        "kind": "RECOVER_TICK",
        "pick": 0,
    }


# ----- full-mode process-fault plan ------------------------------------------------------------


def test_process_fault_plan_is_deterministic_and_ordered() -> None:
    plan = plan_process_faults(1, 20_000)
    assert plan == plan_process_faults(1, 20_000)
    assert plan != plan_process_faults(2, 20_000)
    positions = [fault.at_op for fault in plan]
    assert positions == sorted(positions) and len(set(positions)) == len(positions)
    assert all(1_000 <= position <= 18_000 for position in positions)


def test_process_fault_plan_mixes_kills_and_postgres_restarts() -> None:
    plan = plan_process_faults(1, 2_000_000)
    kinds = Counter((fault.action, fault.service) for fault in plan)
    assert kinds[("restart_postgres", "postgres")] >= 1
    assert kinds[("kill", "poller")] > 0 and kinds[("kill", "api")] > 0
    assert kinds[("kill", "poller")] > kinds[("kill", "api")]
    small = plan_process_faults(1, 400)
    assert any(fault.action == "restart_postgres" for fault in small)
    assert len(small) >= 2


def test_process_fault_commands_use_both_compose_files_and_never_stop_postgres() -> None:
    plan = plan_process_faults(3, 5_000)
    for fault in plan:
        for command in fault.commands():
            assert command[:2] == ["docker", "compose"]
            assert command.count("-f") == 2
            assert "stop" not in command and "down" not in command
        if fault.action == "kill":
            first, second = fault.commands()
            assert first[-3:] == ["-s", "SIGKILL", fault.service]
            assert second[-2:] == ["start", fault.service]
        else:
            assert fault.commands() == [compose("restart", "postgres")]
    assert len(format_plan(plan)) == len(plan)


def test_plan_handles_tiny_runs() -> None:
    assert plan_process_faults(1, 10) is not None
