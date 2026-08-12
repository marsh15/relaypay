# Terminal subscription recovery suppresses every later action

Status: accepted for v0.13.0. A recovery case owns all of its payment retries, messages, and timers;
payment success, opt-out, policy expiry, a non-retryable failure, or exhausted attempts terminates
the case and atomically suppresses every remaining action. Provider calls still occur outside the
transaction: a send-before-network action record is the recovery fence, and any ambiguous or
crash-reclaimed action performs status lookup only. This favors a missed synthetic recovery action
over the harder-to-detect harm of a duplicate payment or unwanted message.
