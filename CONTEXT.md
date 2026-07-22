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
