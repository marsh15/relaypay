"""PSP webhook ordering decision (implemented by Claude at the maintainer's request).

Written against the specification and the pre-existing tests in
tests/unit/test_psp_ordering_spec.py; see PROVENANCE.md.
"""

from relaypay.psp.types import Apply, Decision, Ignore, NormalizedPspEvent, OperationView, Review

TERMINAL_STATUSES = frozenset({"SUCCEEDED", "FAILED"})
# The operation status each non-pending event outcome corresponds to.
AGREEING_STATUS = {"SUCCEEDED": "SUCCEEDED", "DECLINED": "FAILED"}


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
    if event.event_id in view.applied_event_ids:
        return Ignore("DUPLICATE")
    if event.outcome == "PENDING":
        return Ignore("STALE")
    if view.status not in TERMINAL_STATUSES:
        return Apply(event.outcome)
    if AGREEING_STATUS[event.outcome] == view.status:
        return Ignore("DUPLICATE")
    return Review("CONTRADICTS_TERMINAL_STATE")
