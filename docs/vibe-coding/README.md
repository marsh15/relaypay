# RelayPay Vibe-Coding Brief

This directory is the implementation source of truth for RelayPay. Read the documents in order before changing code:

1. [01-prd.md](./01-prd.md) — product contract, scope, stories, and success measures
2. [02-trd.md](./02-trd.md) — frozen stack, architecture, protocols, and constraints
3. [03-app-flow.md](./03-app-flow.md) — API, console, worker, and recovery journeys
4. [04-ui-ux-design-brief.md](./04-ui-ux-design-brief.md) — visual and interaction direction
5. [05-backend-schema.md](./05-backend-schema.md) — data model, relationships, constraints, and access rules
6. [06-implementation-plan.md](./06-implementation-plan.md) — six-week dependency order and release gates

## Project Contract

RelayPay is an INR-only, multi-tenant payment-orchestration sandbox built to demonstrate backend and fintech correctness under retries, races, worker crashes, and ambiguous provider responses.

The primary proof is:

```text
provider completes capture
→ RelayPay loses the response
→ operation remains PROCESSING
→ recovery queries by stable provider key
→ one capture, journal, event, and webhook delivery
→ every bound idempotency key returns the same terminal response bytes
```

## Document Authority

- The PRD owns product scope and non-goals.
- The TRD owns stack and correctness protocols.
- The App Flow owns routes, journeys, redirects, and UI states.
- The UI/UX brief owns presentation and interaction behavior.
- The Backend Schema owns persistence, constraints, and access rules.
- The Implementation Plan owns build order, cut policy, and release gates.
- When two documents appear inconsistent, choose the interpretation that preserves financial correctness, tenant isolation, immutable evidence, and the PRD's explicit non-goals; then update both documents in the same change.

## Global Assumptions

- Six weeks means 15–20 focused hours per week.
- Architecture and core correctness behavior are frozen; failing tests and implementation evidence resolve remaining uncertainty.
- Only synthetic INR test data is accepted.
- The public repository is reproducible with Docker Compose.
- Two demo organisations are seeded with separately authenticated administrators and merchant API keys.
- A CLI/README demonstration is the fallback release surface if the week-four backend gate is late.
- PostgreSQL row-level security is intentionally out of scope; tenant isolation is enforced by application queries and composite database keys.
- The UI palette and typography in the design brief are implementation assumptions because the product contract did not prescribe a brand system.

## Implementation-Changing Open Questions

None. Default choices are recorded as assumptions so an implementation agent can begin without waiting for clarification.
