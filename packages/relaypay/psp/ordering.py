"""PSP webhook ordering decision: HAND-WRITTEN BY THE MAINTAINER (see PROVENANCE.md).

AI tooling wrote only this interface, its specification and the red tests in
tests/unit/test_psp_ordering_spec.py. The implementation is the maintainer's.
"""

from relaypay.psp.types import Decision, NormalizedPspEvent, OperationView


def decide(view: OperationView, event: NormalizedPspEvent) -> Decision:
    """Decide what one verified PSP event means for the operation it names.

    Specification (pure; no I/O; deterministic):

    1. ``event.event_id`` in ``view.applied_event_ids`` -> ``Ignore("DUPLICATE")``.
    2. Operation non-terminal (PROCESSING or REQUIRES_REVIEW):
       * outcome SUCCEEDED -> ``Apply("SUCCEEDED")``; outcome DECLINED -> ``Apply("DECLINED")``;
       * outcome PENDING -> ``Ignore("STALE")`` (nothing to apply; polling continues).
    3. Operation terminal (SUCCEEDED or FAILED):
       * outcome agrees (SUCCEEDED with SUCCEEDED, DECLINED with FAILED)
         -> ``Ignore("DUPLICATE")``;
       * outcome PENDING -> ``Ignore("STALE")`` (a late, out-of-order report);
       * outcome contradicts (SUCCEEDED vs FAILED, or DECLINED vs SUCCEEDED)
         -> ``Review("CONTRADICTS_TERMINAL_STATE")``.

    Consequence the tests check: applying any permutation of an operation's events, each
    delivered up to three times, through ``decide`` plus the finalizer's terminal no-op always
    reaches the same final state as applying the events once, in order.
    """
    raise NotImplementedError("hand-written by the maintainer; see PROVENANCE")
