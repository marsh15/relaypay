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

## Settlement intelligence

**Settlement Policy**:
An immutable, versioned timing rule for one Merchant Account: one IANA timezone, one daily cutoff,
a T+0/T+1/T+2 weekday delay, and include-or-skip weekends.
_Avoid_: Payout schedule, editable rule

**Settlement Forecast**:
One immutable pre-cutoff snapshot of expected capture, refund, receivable-offset, and settlement
totals for a business date; a re-forecast appends a new sequence.
_Avoid_: Settlement projection, mutable forecast

**Forecast Line Item**:
A deterministic capture, refund, or receivable-offset entry bound to one Settlement Forecast.
_Avoid_: Estimate, model output

**Settlement Question**:
One idempotent operator question answered by fixed tenant-scoped queries with citations; the model
may only classify intent and dates and narrate validated results.
_Avoid_: Chat, free-form query

**Clarification**:
The typed response returned when a Settlement Question is unsupported or ambiguous.
_Avoid_: Error, refusal

## Merchant risk review

**Onboarding Snapshot**:
An immutable code-defined synthetic capture of one merchant site: HTML, catalogue, policies,
contacts, WHOIS-like domain data, pricing, and claims.
_Avoid_: Crawl, uploaded evidence

**Risk Review**:
One immutable evaluation of an Onboarding Snapshot under one score version.
_Avoid_: Investigation, case

**Risk Finding**:
A deterministic check result or a model claim finding with a verbatim quote and source path.
_Avoid_: Suspicion, annotation

**Risk Escalation**:
A hard-stop or severity-driven reviewer queue entry bound to one review version.
_Avoid_: Alert, blocking rule

**Disposition**:
A reviewer decision on an escalation that never rewrites evidence, findings, or scores.
_Avoid_: Override, score edit


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

## Subscription recovery

**Subscription**:
A synthetic recurring commercial agreement that owns consent and the expected invoice amount.
_Avoid_: Recurring payment, plan

**Invoice**:
An immutable request for one subscription payment within a billing period.
_Avoid_: Charge, bill

**Recurring Payment Attempt**:
Immutable provider evidence for one numbered attempt to pay an invoice.
_Avoid_: Retry, transaction

**Recovery Case**:
The bounded, terminal-safe workflow opened by one verified failed recurring payment attempt.
_Avoid_: Dunning campaign, retry loop

**Scheduled Action**:
A policy-authorized payment retry or message that has not yet produced an external effect.
_Avoid_: Job, timer

**Communication Record**:
Immutable evidence of one idempotent synthetic message delivery attempt.
_Avoid_: Notification

**Opt-Out**:
A customer instruction that terminates the entire recovery case and suppresses every later action.
_Avoid_: Channel preference, unsubscribe flag
