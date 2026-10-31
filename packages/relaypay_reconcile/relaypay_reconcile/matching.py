"""Set-based matching: the findings table is filled by SQL over per-reference aggregates.

A reference is one (kind, ref) pair. Each side is first reduced to one row per reference. The row
count gives the duplicate and missing checks. Where a reference has exactly one row on each side,
the aggregate values are that row's values, so the pair checks read only the aggregates. A
duplicate never produces a spurious amount difference.
"""

import sqlite3

from relaypay_reconcile.mapping import Mapping

_CREATE_FINDINGS = (
    "CREATE TABLE findings (exception TEXT NOT NULL, kind TEXT NOT NULL, ref TEXT NOT NULL, "
    "side TEXT NOT NULL, expected INTEGER, actual INTEGER, detail TEXT NOT NULL)"
)

# One row per (kind, ref). n is the row count. failed is 0 when at least one row settles or is
# expected to settle. amount, fee, tax and day are the largest value seen, which is exact when n = 1
# (MAX ignores NULLs, so a single blank fee stays NULL). The pair step then never reads the row
# tables again.
_CREATE_KEYS = (
    "CREATE TABLE skeys (kind TEXT NOT NULL, ref TEXT NOT NULL, n INTEGER NOT NULL, "
    "failed INTEGER NOT NULL, amount INTEGER NOT NULL, fee INTEGER, tax INTEGER, day INTEGER, "
    "PRIMARY KEY (kind, ref)) WITHOUT ROWID",
    "INSERT INTO skeys SELECT kind, ref, COUNT(*), MIN(failed), MAX(amount), MAX(fee), MAX(tax), "
    "MAX(day) FROM settlement GROUP BY kind, ref",
    "CREATE TABLE okeys (kind TEXT NOT NULL, ref TEXT NOT NULL, n INTEGER NOT NULL, "
    "failed INTEGER NOT NULL, amount INTEGER NOT NULL, fee INTEGER, tax INTEGER, day INTEGER, "
    "PRIMARY KEY (kind, ref)) WITHOUT ROWID",
    "INSERT INTO okeys SELECT kind, ref, COUNT(*), MIN(failed), MAX(amount), MAX(fee), MAX(tax), "
    "MAX(day) FROM orders GROUP BY kind, ref",
)

# One row per reference that has exactly one row on each side and a landed settlement row.
_CREATE_PAIRS = """
CREATE TABLE pairs AS
SELECT ok.kind AS kind, ok.ref AS ref, ok.failed AS o_failed,
       ok.amount AS o_amount, sk.amount AS s_amount,
       ok.fee AS o_fee, sk.fee AS s_fee, ok.tax AS o_tax, sk.tax AS s_tax,
       ok.day AS o_day, sk.day AS s_day
FROM okeys AS ok
JOIN skeys AS sk ON sk.kind = ok.kind AND sk.ref = ok.ref
WHERE ok.n = 1 AND sk.n = 1 AND sk.failed = 0
"""

_FINDINGS = (
    """INSERT INTO findings
    SELECT 'DUPLICATE', kind, ref, 'settlement', NULL, NULL,
           printf('%d settlement rows share this reference', n)
    FROM skeys WHERE n > 1""",
    """INSERT INTO findings
    SELECT 'DUPLICATE', kind, ref, 'orders', NULL, NULL,
           printf('%d order rows share this reference', n)
    FROM okeys WHERE n > 1""",
    """INSERT INTO findings
    SELECT CASE WHEN kind = 'refund' THEN 'REFUND_NOT_LANDED' ELSE 'MISSING_IN_SETTLEMENT' END,
           kind, ref, 'orders', CASE WHEN n = 1 THEN amount END, NULL,
           CASE WHEN kind = 'refund' THEN 'no landed refund row in the settlement'
                ELSE 'no landed settlement row for this order' END
    FROM okeys AS o
    WHERE o.failed = 0
      AND NOT EXISTS (SELECT 1 FROM skeys AS s
                      WHERE s.kind = o.kind AND s.ref = o.ref AND s.failed = 0)""",
    """INSERT INTO findings
    SELECT 'MISSING_IN_ORDERS', kind, ref, 'settlement', NULL, CASE WHEN n = 1 THEN amount END,
           'no order with this reference'
    FROM skeys AS s
    WHERE NOT EXISTS (SELECT 1 FROM okeys AS o WHERE o.kind = s.kind AND o.ref = s.ref)""",
    """INSERT INTO findings
    SELECT 'AMOUNT_MISMATCH', kind, ref, 'both', o_amount, s_amount,
           printf('ordered %d paise, settled %d paise', o_amount, s_amount)
    FROM pairs
    WHERE o_failed = 0 AND ABS(o_amount - s_amount) > :tolerance""",
    """INSERT INTO findings
    SELECT 'FEE_DIFFERENCE', kind, ref, 'both', o_fee, s_fee,
           printf('expected fee %d paise, settled fee %d paise', o_fee, s_fee)
    FROM pairs
    WHERE kind = 'payment' AND o_failed = 0
      AND o_fee IS NOT NULL AND s_fee IS NOT NULL AND ABS(o_fee - s_fee) > :tolerance""",
    """INSERT INTO findings
    SELECT 'GST_DIFFERENCE', kind, ref, 'both', o_tax, s_tax,
           printf('expected tax %d paise, settled tax %d paise', o_tax, s_tax)
    FROM pairs
    WHERE kind = 'payment' AND o_failed = 0
      AND o_tax IS NOT NULL AND s_tax IS NOT NULL AND ABS(o_tax - s_tax) > :tolerance""",
    """INSERT INTO findings
    SELECT 'TIMING', kind, ref, 'both', NULL, NULL,
           printf('settled %d day(s) after the order date, window is %d to %d',
                  s_day - o_day, :min_days, :max_days)
    FROM pairs
    WHERE kind = 'payment' AND o_failed = 0 AND o_day IS NOT NULL AND s_day IS NOT NULL
      AND (s_day - o_day < :min_days OR s_day - o_day > :max_days)""",
    """INSERT INTO findings
    SELECT 'FAILED_BUT_DEBITED', kind, ref, 'both', 0, s_amount,
           'order status is failed but the settlement shows a landed debit'
    FROM pairs
    WHERE kind = 'payment' AND o_failed = 1""",
)


def reconcile(conn: sqlite3.Connection, mapping: Mapping) -> None:
    """Fill the findings table. Every statement runs inside SQLite; no rows pass through Python."""
    params = {
        "tolerance": mapping.tolerance_paise,
        "min_days": mapping.timing_window_days[0],
        "max_days": mapping.timing_window_days[1],
    }
    conn.execute("BEGIN")
    for statement in _CREATE_KEYS:
        conn.execute(statement)
    # The row tables are no longer read. Dropping them frees their pages for the steps below,
    # which keeps the peak size of the temporary database down on large inputs.
    conn.execute("DROP TABLE settlement")
    conn.execute("DROP TABLE orders")
    conn.execute(_CREATE_PAIRS)
    conn.execute(_CREATE_FINDINGS)
    for statement in _FINDINGS:
        conn.execute(statement, params)
    conn.execute("COMMIT")
