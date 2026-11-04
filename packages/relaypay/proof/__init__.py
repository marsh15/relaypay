"""Proof harness: capture a point-in-time SystemSnapshot and check the money invariants on it.

``snapshot_types`` and ``snapshot`` capture the state; ``invariants`` checks it (see PROVENANCE
for who wrote which module).
"""

from relaypay.proof.invariants import check_invariants
from relaypay.proof.snapshot import capture_snapshot
from relaypay.proof.snapshot_types import (
    AckedOp,
    InvariantCode,
    SystemSnapshot,
    Violation,
)

__all__ = [
    "AckedOp",
    "InvariantCode",
    "SystemSnapshot",
    "Violation",
    "capture_snapshot",
    "check_invariants",
]
