# RelayPay Domain Language

RelayPay is an evidence-first synthetic payment orchestration system. These terms distinguish
provider facts, internal financial facts, and operator workflow without treating annotations as
financial truth.

## Reconciliation

**Statement Export**:
An immutable provider-produced snapshot of provider effects for one account and bounded period.
_Avoid_: Report, live statement

**Statement Import**:
The immutable raw bytes and identity of one provider statement accepted into one RelayPay
environment.
_Avoid_: Upload, reconciliation file

**Statement Item**:
An immutable normalized provider fact parsed from a Statement Import.
_Avoid_: Transaction, payment

**Reconciliation Run**:
One algorithm-versioned comparison of a Statement Import with RelayPay's internal evidence.
_Avoid_: Reconciliation, job

**Match**:
Immutable evidence that a Statement Item agrees with the linked internal operation and financial
evidence under a specific Reconciliation Run.
_Avoid_: Resolution

**Mismatch**:
An operator workflow around a deterministic disagreement or missing fact found by a
Reconciliation Run.
_Avoid_: Error, failure

**Mismatch Evidence Version**:
An immutable snapshot of the facts supporting a Mismatch at one point in time.
_Avoid_: Mismatch update

**Acknowledgement**:
An operator state asserting that a Mismatch has been reviewed, without changing its evidence or
financial truth.
_Avoid_: Approval

**Resolution**:
An operator state closing a Mismatch with a note or link to an existing compensating journal; it
does not create or alter a financial outcome.
_Avoid_: Reconciliation fix

## Merchant balances and settlement

**Merchant Account**:
An environment-scoped financial account that owns one set of payable, receivable, and payout
ledger templates. Exactly one active Merchant Account is the default for existing `/api/v1`
payment creation.
_Avoid_: Organisation, ledger account

**Pending Payable**:
Captured merchant value that remains attributable to unsettled captures.
_Avoid_: Balance row, cash

**Available Payable**:
Settled merchant value eligible for future payout reservation after receivable offsets.
_Avoid_: Bank balance, cash

**Merchant Receivable**:
Value owed by the merchant after valid refunds exceed pending and available payable positions.
_Avoid_: Negative balance mutation

**Settlement Run**:
A route-idempotent, immutable-outcome command that claims eligible capture value once for one
Merchant Account.
_Avoid_: Payout, reconciliation run

**Settlement Item**:
Immutable evidence binding one capture amount to its Settlement Run and settlement journal.
_Avoid_: Mutable settlement line

**Balance Transaction**:
An immutable projection of one originating journal into pending, available, receivable, and
payout-clearing deltas. It is reconstructable evidence, never balance authority.
_Avoid_: Balance, counter

**Phase 2 Opening Journal**:
One deterministic journal per default Merchant Account and environment that transfers the net
legacy merchant-payable position into Pending Payable without changing old journals or postings.
_Avoid_: Backfill rewrite

## Agent operations

**Business Event**:
An immutable tenant-scoped fact recorded transactionally in PostgreSQL and published at least once
to derived streams.
_Avoid_: Kafka message, task

**Workflow Definition**:
An immutable code-reviewed description of allowed steps, retry policy, approvals, and budgets.
_Avoid_: Prompt, editable automation

**Workflow Run**:
The durable execution record for one activated Workflow Definition and trigger event.
_Avoid_: Celery task, agent session

**Workflow Step**:
One leased, retryable unit of a Workflow Run whose state is authoritative in PostgreSQL.
_Avoid_: Network request, queue message

**Artifact**:
Bounded immutable synthetic bytes or canonical JSON produced or selected by a Workflow Run.
_Avoid_: Model response, mutable document

**Dead Letter**:
Immutable evidence that event publication, consumption, or a Workflow Step exhausted its policy.
_Avoid_: Error log, failed queue item

## Dispute response

**Dispute Case**:
A tenant-scoped synthetic network claim against one payment whose source snapshot and terminal
submission identity never change.
_Avoid_: Workflow run, refund

**Dispute Draft**:
An immutable analyst- or agent-authored response version; an edit creates a successor and
invalidates approval of every earlier package.
_Avoid_: Mutable response, model output

**Dispute Package**:
Exact canonical manifest, response, and attachment bytes bound to one draft and one digest.
_Avoid_: Draft, regenerated ZIP

**Dispute Submission**:
One externally visible synthetic-network effect keyed by the immutable package identity.
_Avoid_: HTTP attempt, retry
