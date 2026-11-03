# Confirmed findings

No candidate met the final confirmed-vulnerability bar. Conditional records are in NEEDS-VALIDATION.md. This is not a clean bill of health.

# Rejected-claim appendix

## Recurring-failure replay disclosure is unreachable through the shipped route

`subscriptions-intake:replay-unbound-consumption`

A bounded isolated check using the target envelope model rejected a route-shaped dictionary with a datetime_type error at occurredAt; replacing only that value with an aware Python datetime was accepted. The promoted observation is agents/terra_verify_replay_r2/artifacts/result.txt. Consequently the first request cannot reach the consumption marker or the claimed response through the identified route. The unscoped direct-service lookup remains a source-level defect, but no source-visible lower-trust direct caller and disclosure boundary establish the proposed impact.
