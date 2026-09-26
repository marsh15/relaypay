# Merchant risk review scores risk deterministically with cited model findings

Status: accepted for v0.15.0. The synthetic merchant-site service exposes immutable, code-defined
snapshots only; there is no crawling and no uploaded merchant evidence. Deterministic checks score
five risk dimensions — completeness (0–20), domain identity and age (0–20), category and content
(0–30), pricing (0–15) — as unearned maxima, so a trustworthy site scores zero and the worst scores
one hundred; model claim findings add five risk points each up to fifteen. Severity bands are
LOW 0–29, MEDIUM 30–59, HIGH 60–79, and CRITICAL 80–100. Prohibited-category, impersonation,
counterfeit, and guaranteed-return findings hard-stop and escalate the review regardless of the
total score; HIGH and CRITICAL severities escalate otherwise. The model may only return
classifications with verbatim snapshot quotes and a source path; unattributable whole-page
findings are LOW confidence and never hard-stop alone, one direct source is MEDIUM, and
deterministic evidence or two-source corroboration is HIGH. Reviewers annotate and disposition
escalations but can never rewrite snapshots, findings, or calculated scores.
