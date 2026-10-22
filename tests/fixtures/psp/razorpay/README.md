# Razorpay fixtures

shapes from public docs, UNVERIFIED against real test-mode payloads; maintainer to confirm

The webhook JSON fixtures follow the documented payment and refund event envelope. The settlement
CSV uses an assumed normalized header (`reference,payment_id,refund_id,amount,fee,tax,currency,settled_at`);
the public docs do not specify a stable downloadable CSV schema, so its header must be confirmed.
All report money fields are integer paise, matching Razorpay API entity amounts, fees and tax.

Official docs:

- https://razorpay.com/docs/webhooks/validate-test/
- https://razorpay.com/docs/webhooks/payments/
- https://razorpay.com/docs/webhooks/refunds/
- https://razorpay.com/docs/payments/settlements/settlement-recon/
- https://razorpay.com/docs/api/payments/route/fetch-transfers-for-a-settlement/

Fixtures contain synthetic identifiers and no credentials.
