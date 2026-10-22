shapes from public docs, UNVERIFIED against real test-mode payloads; maintainer to confirm

Sources:

- https://www.cashfree.com/docs/payments/online/webhooks/signature-verification
- https://www.cashfree.com/docs/api-reference/payments/latest/payments/webhooks
- https://www.cashfree.com/docs/api-reference/payments/latest/refunds/webhooks

The settlement CSV fixture uses the adapter's assumed normalized columns: `reference`,
`payment_id`, `refund_id`, `amount`, `fee`, `gst`, `currency`, and `settled_at`. This is a
test schema, not a claim that Cashfree exports these exact column names.
