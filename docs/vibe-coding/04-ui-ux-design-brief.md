# 04 — UI/UX Design Brief

## Design Intent

RelayPay should feel like a forensic instrument, not a generic fintech dashboard. The interface exists to make causality, uncertainty, and invariants legible: what committed, what is only in progress, what the provider proved, and why RelayPay did or did not create a financial effect.

Use restrained visual design, dense but readable evidence, explicit timestamps and IDs, and strong state language. Never imply correctness through color alone.

## Aesthetic

- Technical, calm, evidence-first, and slightly editorial.
- Light canvas with dark “inspection surface” panels for raw evidence and timelines.
- Flat layers, crisp one-pixel borders, minimal shadow, no gradients, glass effects, or decorative finance imagery.
- Monospaced treatment is reserved for IDs, paise, timestamps, digests, JSON, and state-machine evidence—not general body text.
- Favor a small number of strong proof statements over a dashboard grid of vanity metrics.

## Palette

This is the default implementation palette; it may be tokenized and adjusted only after contrast checks.

| Token | Value | Use |
|---|---|---|
| `canvas` | `#F4F1EA` | warm page background |
| `surface` | `#FFFFFF` | primary panels |
| `surface-muted` | `#E9E5DC` | secondary regions/table headers |
| `ink` | `#171A1F` | primary text |
| `ink-muted` | `#5B616B` | supporting text |
| `border` | `#C8C3B8` | dividers and component outlines |
| `brand` | `#174A3A` | navigation, focus-compatible accents |
| `action` | `#0B63CE` | links and primary actions |
| `success` | `#176B45` | verified success paired with icon/text |
| `processing` | `#8A5A00` | in-progress/uncertain paired with icon/text |
| `danger` | `#A52A2A` | verified failure/destructive warning |
| `review` | `#6842A0` | requires-review state |
| `code-bg` | `#12161C` | evidence/JSON surface |
| `code-ink` | `#E7EDF5` | text on evidence surface |

Do not use green for `PROCESSING`. Do not use red for transport ambiguity or review; red means a verified business failure or destructive warning.

## Typography

- UI/body: Geist Sans or system fallback (`Inter`, `ui-sans-serif`, `system-ui`).
- Evidence/code: Geist Mono or `ui-monospace`, `SFMono-Regular`, `monospace`.
- Base font: 16px desktop and mobile; body line height 1.5.
- Page title: 32/38, weight 650; section title: 20/28, weight 650; label: 13/18, weight 600.
- Limit explanatory prose to approximately 70 characters per line.
- Use tabular numerals for paise, counts, timestamps, and retry values.

## Layout

### Login

- Single centered authentication panel, maximum width 420px.
- Product claim and synthetic-data warning remain visible without scrolling on common laptop screens.
- Seeded demo identities may be offered as accessible fill controls, never embedded as production-looking credentials.

### Scenario Lab

- Maximum content width 1120px with a two-column desktop layout.
- Left: scenario selection and fault explanation.
- Right: active run/proof panel with stepper, assertions, and evidence link.
- Below: recent runs for the current browser/session only if useful; do not build a general run database table solely for this list.

### Payment Evidence

- Maximum content width 1280px.
- Sticky proof summary beneath the top bar on wide screens.
- Desktop: narrow anchored contents rail plus one main evidence column.
- Mobile/tablet: one column; contents move into a section sheet.
- Evidence groups use vertical rhythm and headings, not a masonry/bento dashboard.

## Component Language

- Corners: 6px for controls, 8px for panels; pills only for compact state labels.
- Borders: one-pixel neutral; use shadow only for dialogs/sheets.
- Buttons: primary solid blue, secondary outlined, tertiary text; destructive styles only for logout/replay warnings where appropriate.
- Minimum interactive target: 44×44 CSS pixels.
- Tables: sticky headers where useful, right-aligned numeric columns, horizontal scrolling with visible affordance on small screens.
- Definition lists: default for IDs, state, timestamps, correlation values, and operation facts.
- Code/evidence blocks: dark surface, wrap toggle, copy control with confirmation, redaction indicator.
- State badges: icon + label + optional reason; never color alone.
- Timeline: vertical sequence with absolute timestamp, relative duration, actor, action, and result.
- Invariant row: icon, assertion sentence, observed value, expected value, and expandable evidence link.

## State Vocabulary

Use exact backend terms in technical evidence and plain-language companions in summaries:

| Backend state | Summary label | Tone |
|---|---|---|
| `PROCESSING` | In progress — outcome not yet known | amber, neutral copy |
| `SUCCEEDED` | Verified succeeded | green |
| `FAILED` | Verified provider decline | red |
| `REQUIRES_REVIEW` | Needs evidence review | purple |
| `DELIVERED` | Webhook acknowledged | green |
| `DEAD_LETTER` | Delivery retries exhausted | red |

Never translate `PROCESSING`, timeout, 5xx, malformed response, or connection reset into “failed.”

## Key Patterns

### Proof Summary

Lead with the primary claim and observable counts:

```text
Verified: one provider capture effect
1 capture · 1 balanced journal · 1 event · 1 delivered webhook
2 attached keys · terminal responses byte-identical
```

Each item links to the evidence section that substantiates it.

### Refund Availability

Render an equation with text labels and paise/INR formatting:

```text
₹1,000.00 captured
− ₹200.00 succeeded
− ₹150.00 processing/review reserved
= ₹650.00 available
```

Keep the raw paise values available in evidence details.

### Uncertainty

An uncertain operation gets a bordered explanatory callout containing:

- what RelayPay observed;
- what is not yet known;
- why mutation will not be resent;
- the next automatic/manual status-only action.

### Byte Stability

Show response digests and a human-readable equality assertion. Raw stored response bytes may be rendered as decoded sanitized JSON when valid, but must not be reserialized for comparison.

## Motion and Feedback

- Motion is optional and functional: 120–180ms opacity/transform transitions for menus, dialogs, and disclosure content.
- Respect `prefers-reduced-motion`; progress must remain understandable without animation.
- Do not animate monetary totals or continuously pulse processing states.
- Announce scenario steps and mutation outcomes through a polite live region.
- Copy actions change label to “Copied” briefly and preserve focus.

## Responsive Behavior

- Break content based on available width, not device names.
- At narrow widths, proof metrics stack, tables scroll, and timeline facts wrap below labels.
- Never truncate IDs without a copy/full-value affordance.
- Dialogs become near-full-width with safe viewport margins; long content scrolls inside the dialog body.
- Primary scenario action remains visible after its explanation; avoid sticky actions that cover evidence.
- Test at 320px width, 200% zoom, and landscape mobile.

## Accessibility

- Meet WCAG 2.2 AA for contrast and interaction behavior.
- Provide a skip link, semantic landmarks, one page-level heading, and ordered heading hierarchy.
- All controls are keyboard reachable with a clearly visible focus indicator of at least 2px.
- State, validation, balance, and scenario outcomes use text/icon/structure in addition to color.
- Dialogs trap focus, name themselves, close with Escape when safe, and return focus to the opener.
- Tables have captions and proper header scope; complex evidence may use definition lists instead.
- Form errors are associated with fields and summarized on submit.
- Polling does not steal focus or reset expanded disclosures.
- Timestamps expose timezone (`UTC`) and machine-readable `<time>` values.
- Amounts have unambiguous currency and accessible labels.
- Auto-updating regions have pause/reduce controls if updates become frequent.

## Content Rules

- Say “verified provider decline,” not “payment failed,” when precision matters.
- Say “outcome not yet known,” not “something went wrong,” for ambiguous transport results.
- Pair internal codes with a short explanation: `PROVIDER_INDETERMINATE — status lookup did not produce verifiable evidence`.
- Mark all data as synthetic in Login, Scenario Lab, and API documentation.
- Explain retries as safe only within the documented idempotency contract.
- Never claim distributed exactly once; say “one provider effect by stable provider key” and “at-least-once delivery with idempotent handling.”

## Reference Direction

- Borrow the evidence density and copyable identifiers of excellent developer tooling.
- Borrow the calm hierarchy and restrained color of editorial data products.
- Do not copy consumer banking tropes, trading charts, neon gradients, glass cards, or generic KPI dashboards.

## Design Done Criteria

- The three console routes are usable with keyboard only and at 320px/200% zoom.
- Every backend state has consistent text, icon, and token treatment.
- The primary lost-response proof is understandable before expanding raw evidence.
- A reviewer can move from each assertion to its supporting operation, journal, event, or delivery evidence.
- No secret-bearing or cross-tenant field is rendered.
- Automated accessibility checks pass and a manual focus/semantics pass is recorded.
