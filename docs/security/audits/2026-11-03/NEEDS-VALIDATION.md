# Needs validation

These records are conditional source-grounded hypotheses, not confirmed vulnerabilities. No severity is assigned. Deployment checks below are owner observation; do not probe live or shared services.

## Login admission trusts the first forwarded address if it reaches the API

Fingerprint: `api-session-login-untrusted-xff-limiter-key`

The unauthenticated login handler uses the first X-Forwarded-For item as its process-local rate-limit key. If an untrusted caller can select that item through a supported ingress path, the caller could use different budgets or consume a budget associated with another address. Admitted requests perform a user lookup and password verification, but the snapshot does not establish external reachability, effective ingress normalization, upstream limits, or a meaningful shared availability impact.

Blockers:

- The actual ingress topology and trusted-proxy settings are unknown, including whether an externally supplied first X-Forwarded-For item reaches either the API directly or the console proxy.
- Effective upstream admission limits, process topology and shared login capacity are unknown, so a meaningful shared availability effect is not established.

Safe validation:

- deployment: Have the owner inspect the effective ingress routes and trusted-proxy configuration to determine whether an untrusted caller can control the first X-Forwarded-For value received by the API on a supported route. Have the owner inspect applicable upstream quotas, process topology and shared login capacity to assess whether independent budgets can affect operator login availability.

Source trace:

- `apps/console/app/backend/[...path]/route.ts:13` — The console proxy copies a present X-Forwarded-For header into its request to the API; POST is exported through this proxy.
- `apps/api/main.py:324` — The session login endpoint takes the first comma-separated forwarded value, falling back to the request peer when the selected value is empty, and passes the result to the limiter before issuing a session.
- `packages/relaypay/identity/rate_limit.py:23` — The process-local limiter maintains and enforces a separate event deque for each supplied key.
- `packages/relaypay/identity/security.py:104` — An admitted attempt looks up active users by normalized email and verifies either a dummy password hash for a non-unique result or the matching user's password hash.


## Metrics scrapes synchronously refresh database-wide gauges

Fingerprint: `apps/api/main.py:metrics-scrape-database-amplification`

When observability is enabled, an unauthenticated requester able to reach the scrape handler can cause database-wide counts and connector enumeration on the API's shared engine. Whether this causes meaningful contention with tenant operations depends on deployment access controls, data size and query cost.

Blockers:

- Effective observability enablement, external routing to the console proxy or scrape handler, and upstream authentication, rate and concurrency limits are not established by source.
- Actual database cardinalities, query plans and competing workload determine whether the shared-engine work has meaningful impact; contention and recovery have not been observed.

Safe validation:

- local: In the approved isolated sandbox, instrument a small private dummy PostgreSQL fixture to confirm query count and connector rows processed per scrape, and inspect query plans without load or saturation.
- deployment: The owner should inspect effective observability settings, routes and upstream scrape controls, then review existing database query and pool telemetry during normal scraping for contention and recovery. Do not generate availability stress.

Source trace:

- `apps/console/app/backend/[...path]/route.ts:13` — The catch-all proxy forwards GET requests using the supplied path; /backend/metrics targets the API's /metrics path.
- `apps/api/main.py:296` — If observability is enabled, the handler opens a transaction and refreshes gauges without a principal dependency; the transaction closes before rendering.
- `packages/relaypay/observability/service.py:129` — The refresh executes three mismatch counts, three queue counts and an unfiltered connector query, then updates gauges for each returned connector.


## Dispute submission body has no application-level size constraint

Fingerprint: `apps/dispute_network/main.py:submission-body-buffer`

The synthetic dispute-network endpoint accepts an unauthenticated ZIP-typed request as a bytes body. If a lower-trust peer can reach the service without an effective body limit, sufficiently large requests could consume memory in the counterparty shared by approved operator submissions. A meaningful shared availability effect and lower-trust deployment access have not been established.

Blockers:

- The effective peer access controls, ingress body and concurrency limits, runtime memory behavior, and recovery consequences for a shared counterparty deployment are not established by the source snapshot. These determine whether a lower-trust requester can cause a meaningful effect on another operator submission.

Safe validation:

- deployment: The owner should inspect the effective deployment overlays, network and ingress policies, and runtime configuration to determine who can reach port 8005, whether body and concurrency limits apply before buffering, and how a counterparty interruption affects another operator submission and recovery. Do not probe or disrupt a deployed service.

Source trace:

- `apps/dispute_network/main.py:19` — POST /v1/submissions declares a bytes body and a required Idempotency-Key header without a route-level authentication dependency or body-size constraint.
- `apps/dispute_network/main.py:24` — The handler passes the package bytes and submission key to the resolved network.
- `packages/relaypay/disputes/network.py:19` — The counterparty records a synthetic observation keyed by the submission key and includes the received byte length.


## Direct origin access can bypass edge admission

Fingerprint: `apps/edge/src/index.ts:origin-hmac-default-off-admission-bypass`

Direct origin callers bypass Worker replay, rate, body size and content-type checks if ingress allows access and signature enforcement is disabled. Origin merchant authentication and provider signature checks remain separate; unauthorized payments or webhooks are not established.

Blockers:

- Effective Worker routing, origin ingress, flag value and equivalent upstream limits are unknown; they determine exposure and shared impact.

Safe validation:

- local: In the approved sandbox, compare unsigned requests to a dummy FastAPI route with middleware enforcement false and true and a dummy secret; observe route reachability versus 401.
- deployment: Owner inspects rendered Worker routing, effective flag, ingress policy and upstream limits, and identifies any affected shared resource; do not probe endpoints.

Source trace:

- `apps/api/routes/payments.py:134` — The origin exposes a customer endpoint under /api/v1.
- `packages/relaypay/edge/origin.py:105` — When enforcement is false, requests skip signature verification and reach call_next at line 119.


## Queued webhook may stop reaching the origin after retry exhaustion

Fingerprint: `apps/edge/src/index.ts:queue-exhausted-retries-drop-accepted-webhook`

The optional edge returns 202 after archiving bytes and queueing a reference, before origin authentication or acceptance. If forwarding fails beyond the retry budget and neither the queue nor sender enables recovery, a valid synthetic connector event may remain unprocessed. Archived bytes alone do not establish complete event loss.

Blockers:

- Queue retention and terminal behavior after retry exhaustion, including any externally configured dead-letter or recovery mechanism, are not established by repository source.
- An actual edge deployment, its effective queue settings and sender behavior after 202 are not established.

Safe validation:

- deployment: If an owner operates this edge, ask them to inspect the effective queue consumer, dead-letter, retention and recovery settings and the sender's retry policy after a 202. Ask them to observe existing records or operational evidence for failed deliveries and recovery without generating audit traffic.

Source trace:

- `apps/edge/src/index.ts:133` — A POST matching the connector route enters the webhook handler.
- `apps/edge/src/index.ts:106` — The handler checks header shape, admission and body size, archives bytes, queues a reference and returns 202 before origin delivery.
- `apps/edge/src/index.ts:138` — The consumer acknowledges successful origin responses and requests retry for failed forwards; terminal handling is left to the queue platform.


## Queue retries origin responses that may be permanent

Fingerprint: `apps/edge/src/index.ts:queue-retry-permanent-origin-rejections`

A requester can submit a webhook with shaped headers and an invalid provider signature for queuing. If the queue redelivers an origin-rejected message, each delivery can cause another archive read and origin request, potentially consuming shared capacity.

Blockers:

- The source does not establish a reachable deployment, effective upstream quotas, deployed queue retry settings or redelivery behavior, or whether the extra work materially affects shared capacity.

Safe validation:

- deployment: An owner should inspect the effective route, upstream quotas, queue consumer and retry settings, and existing aggregate queue and origin telemetry to determine whether rejected messages are redelivered and whether the extra work materially affects shared capacity. Do not send test traffic.

Source trace:

- `apps/edge/src/index.ts:106` — The route checks header shapes and admission, then archives and queues the body without verifying the provider signature.
- `apps/edge/src/index.ts:121` — The consumer reads and verifies the archived body, signs an origin request and returns false for non-2xx responses.
- `apps/edge/src/index.ts:138` — A false result requests a retry after two seconds.


## Durable Object replay admission depends on storage input-gate semantics

Fingerprint: `apps/edge/src/index.ts:tenant-coordinator-replay-check-then-put`

If requests can interleave between the coordinator's storage reads and write, identical replay keys could both be admitted, causing two synchronous origin forwards or two webhook queue entries. This does not establish duplicate financial effects.

Blockers:

- The available isolated coordinator capabilities do not specify a Durable Object runtime emulator, so actual storage input-gate interleaving cannot be established by the widened-gap mock.
- The effective Worker route and runtime compatibility configuration are not established by the source snapshot.

Safe validation:

- local: With a compatible isolated Durable Object runtime, send a bounded pair of concurrent identical requests with a valid dummy replay key and timestamp to one coordinator, compare with a sequential pair, and count forwards to a dummy origin.
- deployment: Ask an owner to observe the effective Worker route and compatibility configuration and identify the runtime input-gate semantics applicable to these storage operations.

Source trace:

- `apps/edge/src/index.ts:130` — Routes API requests and connector events to admission.
- `apps/edge/src/index.ts:78` — Validates the replay-key shape and sends it with the timestamp and limits to the named coordinator.
- `apps/edge/src/index.ts:69` — Reads replay and rate state before writing both and allowing the request.


## Caller-selected event IDs partition webhook admission state

Fingerprint: `apps/edge/src/index.ts:webhook-event-id-do-cardinality`

A caller who can reach the optional Worker can submit shape-valid webhook headers and select a different coordinator for each event ID before provider authentication. Accepted requests write coordinator keys and can archive distinct bodies and enqueue references. This could consume resources shared with other users, but deployment exposure and meaningful shared impact remain unknown.

Blockers:

- Owner-observed routing and bindings are needed to establish whether untrusted callers can reach the Worker with its coordinator, archive and queue attached.
- Upstream limits, account quotas, storage lifecycle and recovery, and resource sharing are outside the source and determine whether the allocation pattern has meaningful shared impact.

Safe validation:

- deployment: Ask the owner to inspect existing route and binding configuration, upstream limits, account quotas, storage lifecycle and affected resource sharing without sending traffic or changing resources.

Source trace:

- `apps/edge/src/index.ts:133` — A POST to the connector events path reaches the webhook handler.
- `apps/edge/src/index.ts:109` — Provider headers are shape-checked, then connector ID and caller-supplied event ID select the admission coordinator.
- `apps/edge/src/index.ts:112` — After admission and a one MiB body check, the Worker archives a new body digest and queues a reference.


## Receiver buffers webhook bodies before signature validation

Fingerprint: `apps/receiver/main.py:webhook-unbounded-body`

A requester able to reach the webhook and supply its three required headers can cause the application to request the complete body before timestamp and signature validation. A current timestamp also reaches an HMAC operation that concatenates the timestamp prefix and body. Large or concurrent requests could consume memory in the shared receiver process, but a service interruption and its recovery have not been demonstrated. Invalid signatures are rejected before JSON parsing or receipt insertion.

Blockers:

- Actual receiver exposure, effective ingress and server body-byte, concurrency and receive-time limits, container isolation, and recovery after resource exhaustion are deployment facts. No meaningful shared interruption has been demonstrated.

Safe validation:

- local: In the approved isolated sandbox, use a bounded mocked ASGI fixture with small body chunks and no Content-Length to observe when bytes are consumed relative to validation; inspect the installed server and framework receive path for applicable bounds. Do not create memory pressure.
- deployment: Have the owner inspect effective routing and ingress, proxy and server body-byte, concurrency and receive-time limits, including chunked requests. Review receiver resource isolation and restart behavior in configuration and existing operational records; do not probe or stress a live endpoint.

Source trace:

- `infra/caddy/Caddyfile:11` — The configured Caddy route forwards /webhooks/relaypay to the receiver without an explicit body-size policy.
- `apps/receiver/main.py:42` — The POST route requires event ID, timestamp and signature string headers but does not authenticate them before entering the handler.
- `apps/receiver/main.py:49` — The handler requests the complete body before calling receive_event for timestamp and signature validation.


## Inbound queue draining can delay other tasks in the shared scheduler

Fingerprint: `apps/worker/poller.py:inbound-drain-shared-scheduler-starvation`

The inbound endpoint can add eligible events to a queue that the synchronous poller drains before running later scheduled tasks. The drain has no batch-start cutoff, claim count limit, or deadline, so arrivals during a drain can extend it. Source does not establish that a lower-trust sender can sustain accepted work long enough to cause meaningful shared delay, or that deployed admission and worker topology permit that effect.

Blockers:

- Source does not establish that an independently controlled lower-trust sender has the commerce signing secret, or can obtain a still-fresh valid signed request and reach an admission path.
- Meaningful shared delay depends on accepted arrival rate, processing throughput, effective ingress controls, and whether independent workers continue the affected tasks. Those deployment facts are not established by the sanitized source.

Safe validation:

- local: In the approved isolated sandbox, submit a small finite set of dummy signed events, add one new event while a drain is in progress, and record claim order and the point at which dummy expiry and PSP runners execute. Use a private database and mocked external effects; do not test saturation. This can verify scheduling and admission behavior but cannot establish deployment impact.
- deployment: Have the owner review sender trust and access to fresh signed requests, routing and effective aggregate admission limits, normal accepted-event and drain rates, and worker topology or independent scheduling for expiry, PSP application, and publication. Review existing configuration and operational evidence without generating live load.

Source trace:

- `apps/api/routes/inbound.py:14` — The connector event HTTP endpoint accepts a request body and provider event ID, timestamp, and signature headers, then passes them to admission.
- `packages/relaypay/connectors/service.py:311` — Admission checks timestamp freshness and an HMAC, requires an active connector, and stores a new connector and event-ID pair as pending.
- `packages/relaypay/connectors/service.py:404` — Each claim selects the oldest currently eligible event globally, using a row lock and lease, without a batch-start cutoff.
- `apps/worker/poller.py:108` — The worker repeatedly claims and processes eligible events until a claim returns none.
- `apps/worker/poller.py:160` — The scheduler calls the inbound drain synchronously before later due tasks and before outbox publication; the next loop iteration also waits for it to return.


## Optional observability ports may be reachable outside the operator network

Fingerprint: `compose.production.yaml/observability-profile-host-ports`

If a deployment enables the optional observability profile and permits access from outside the operator network, an unauthenticated network client could reach Prometheus or anonymous Grafana and obtain operational information. Source does not establish that such a deployment or route exists. API operational gauges additionally require OBSERVABILITY_ENABLED to be true and relevant data to exist.

Blockers:

- The sanitized source does not establish whether an actual deployment enables the observability profile, its effective bindings and observability settings, or whether host and cloud network controls permit a lower-trust client to reach the published ports.

Safe validation:

- deployment: The owner should inspect the actual enabled profiles, rendered host bindings, OBSERVABILITY_ENABLED setting, and attached host and cloud network policies to determine whether a principal outside the intended operator network can reach the observability ports.

Source trace:

- `compose.yaml:348` — The optional Prometheus service publishes container port 9090 on host port 9090 without specifying a host IP.
- `compose.production.yaml:35` — The overlay resets core service and simulator ports but contains no override for Prometheus, Grafana, or the collector.
- `infra/observability/prometheus.yaml:6` — Prometheus is configured to scrape the API's internal /metrics endpoint and service and worker metrics.
- `apps/api/main.py:296` — When OBSERVABILITY_ENABLED is true, the endpoint refreshes and renders operational gauges; otherwise it returns 404.
- `packages/relaypay/observability/service.py:129` — The gauges include unscoped mismatch and queue counts and connector public identifiers with circuit states.


## Connector version revocation does not govern inbound admission

Fingerprint: `connector-version-revocation-no-inbound-authority`

A sender retaining the shared commerce control key can submit a fresh event for an active connector after a connector version is revoked. Whether that sender should lose inbound authority on version rotation is unresolved.

Blockers:

- The source does not establish whether version revocation is intended to withdraw inbound authority from a sender that retains the separate shared key, or whether such a sender is permitted to retain it.

Safe validation:

- deployment: Ask the owner to inspect the connector contract and intended key distribution policy to determine whether a revoked version's sender may retain the shared inbound key and submit events. Do not contact a deployed endpoint.

Source trace:

- `apps/api/routes/inbound.py:14` — The connector event route accepts a connector ID, event ID, timestamp, signature and request body.
- `apps/api/routes/inbound.py:23` — The route passes the commerce control secret to admission.
- `packages/relaypay/connectors/service.py:347` — After timestamp and HMAC checks, admission selects an active connector without consulting version status and stores a fresh event as pending.


## Connector webhook bodies are buffered before authentication

Fingerprint: `connectors/inbound-preauth-body-buffering`

A caller able to reach the connector webhook can supply the required headers and cause the origin to buffer the request body before provider timestamp and signature validation. If effective request-size and concurrency limits permit sufficient buffering, requests could exhaust API worker memory and interrupt service. The optional edge also buffers the body before applying its one-MiB check. The source snapshot does not establish that the availability consequence is achievable in a deployment.

Blockers:

- The source snapshot does not establish whether an actual deployment exposes the origin or edge route to an unauthenticated caller, or its effective ingress and runtime body-size, concurrency, and memory limits. These facts determine whether buffering can produce a meaningful availability effect.

Safe validation:

- deployment: Have the owner inspect the connector route's actual network attachment, effective ingress and server streaming body limits, edge admission configuration, concurrency limits, and workload memory limits. Do not send traffic to deployed endpoints.

Source trace:

- `apps/api/routes/inbound.py:14` — The POST connector event endpoint accepts a connector ID and requires provider event, timestamp, and signature headers.
- `apps/api/routes/inbound.py:22` — The route reads the body before calling accept_inbound_webhook for timestamp and signature validation.


## Connector webhook event identity is not bound to its signature

Fingerprint: `connectors/inbound-unsigned-event-identity`

A party that obtains a valid signed connector webhook within its replay window may be able to resubmit the same timestamp and body under a different event ID. Source indicates that this creates a separate queued event and can cause an earlier capture to be applied after a refund to a synthetic commerce payment link. The resulting state transition has not been observed.

Blockers:

- The required bounded observation of the connector acceptance, worker processing, and resulting dummy commerce state was not performed. The prior runtime request was rejected by the provider safety filter, and the narrowed scope prohibits retrying execution.
- Source does not establish that a lower-trust actor can obtain and resubmit a valid request within the replay window through the actual ingress, or establish the connector producer's delivery guarantees. No deployed route or ingress configuration is established.

Safe validation:

- local: An authorized owner can use an isolated, bounded fixture to verify that identical signed capture bytes with a different event ID produce a separate accepted event, then process it after a dummy refund and inspect the single dummy payment link and order. Stop after recording the observed statuses.
- deployment: The owner should review the actual connector producer, transport and ingress configuration to determine delivery guarantees, who can observe and resubmit a signed request within the replay window, and whether any control binds the event ID to authenticated bytes.

Source trace:

- `apps/api/routes/inbound.py:14` — The inbound HTTP endpoint accepts the connector ID in the path and the event ID, timestamp, and signature in separate headers.
- `packages/relaypay/connectors/service.py:338` — The service verifies the timestamp and body HMAC, selects an active connector, and deduplicates by connector and supplied event ID before creating a pending event.
- `apps/worker/poller.py:112` — The worker claims queued events and passes their payload and event ID to commerce synchronization.
- `packages/relaypay/mock_commerce/service.py:89` — The handler selects a payment link by the payload payment ID and applies capture or refund status without an event ordering or deduplication guard.


## Console proxy buffers POST bodies before backend admission

Fingerprint: `console-backend-unbounded-request-buffer`

The console catch-all POST handler reads the complete request body before invoking the backend and contains no handler-level authentication or body-size check before that read. If sufficiently large or concurrent requests can reach it, body buffering could consume shared console process resources and affect other users. No availability impact has been demonstrated.

Blockers:

- The effective ingress and Next.js runtime body-size, concurrency and timeout limits for requests reaching this handler are not established. The stated isolated execution capabilities do not establish availability of the pinned Next.js standalone server and its dependencies for a local runtime check.
- The shared-process effect and recovery under the effective limits are unobserved; source inspection alone does not demonstrate an availability impact.

Safe validation:

- deployment: If a deployment exists, an owner should inspect the actual route attachment, ingress and hosting body-size, concurrency and timeout settings, Next.js runtime limits, console process resource limits and restart policy. Determine whether the combined limits bound per-request and aggregate buffering without sending load to the deployment.

Source trace:

- `apps/console/app/backend/[...path]/route.ts:39` — The catch-all route exports proxy as its POST handler.
- `apps/console/app/backend/[...path]/route.ts:25` — For a POST, proxy awaits request.bytes() while constructing the backend fetch, before that fetch is invoked.


## Requester-controlled HTTP labels may accumulate in shared metrics

Fingerprint: `packages/relaypay/observability/metrics.py:request-label-cardinality`

The API records unmatched request paths and HTTP methods as metric labels. The linked local fixture reports retained children in the shared middleware, but it did not measure process memory or demonstrate degradation. If an ingress permits enough distinct requests to reach a shared API process, accumulated labels could consume resources needed by other users.

Blockers:

- The snapshot does not establish whether an untrusted client can reach a running instance, or what upstream request limits and process recovery settings apply. Those facts determine whether label growth can have a meaningful shared effect in an actual installation.

Safe validation:

- deployment: If an installation exists, ask its owner to inspect the effective route to the API, upstream request and rate limits, process memory limit, and worker recycling or restart policy. Do not send traffic to the installation.

Source trace:

- `infra/caddy/Caddyfile:8` — The supplied production-like Caddy configuration forwards /api/* paths to the API.
- `apps/api/main.py:180` — After a response, the API middleware falls back to request.url.path when no route is present and passes request.method to the metrics observer.
- `packages/relaypay/observability/metrics.py:174` — The shared middleware applies the same route fallback and method passthrough.
- `packages/relaypay/observability/metrics.py:135` — The observer uses method and route as counter and histogram label values.


## Unmatched paths can create distinct durable usage rollups

Fingerprint: `packages/relaypay/observability/record-request-raw-route-usage-rollups`

A requester reaching the API middleware can supply distinct unmatched paths that may create durable anonymous rollup rows in shared database storage. Whether this can cause meaningful shared resource growth depends on ingress and storage controls not established by the snapshot.

Blockers:

- Effective ingress routing and admission, quotas, rollup storage limits and any external cleanup are not established by the source snapshot; their effect on meaningful shared resource growth and recovery is unknown.

Safe validation:

- local: With the isolated python_pg fixture, create the two observability tables, start create_app with dummy settings, send two distinct unmatched GET paths through its HTTP interface and inspect the anonymous rollup keys; stop after observing the rows.
- deployment: An owner should inspect ingress admission and quotas, rollup storage limits and cleanup policies, and aggregate anonymous rollup cardinality and age with read-only, non-sensitive queries. Do not generate load.

Source trace:

- `apps/api/main.py:168` — HTTP requests reaching the API enter the middleware before route authentication.
- `apps/api/main.py:181` — The route key falls back to the request path when no route object is present.
- `apps/api/main.py:211` — Except for health and metrics paths, the middleware passes the route, method and optional principal scope to record_request and catches persistence exceptions.
- `packages/relaypay/observability/service.py:55` — The hourly PostgreSQL upsert includes the truncated route and method in its key.


## Expiry may miss a void committed after its query snapshot

Fingerprint: `payments/expiry.py:nonfailed-void-snapshot-before-payment-lock`

A merchant with payments:write can initiate a void for its scoped authorised, uncaptured payment after the hold has elapsed if expiry has not changed the payment status. Expiry excludes non-failed voids in the same statement that locks payment rows. It remains unverified whether PostgreSQL can select the payment using a stale absence result when the void commits between that statement's snapshot and its attempt to lock the payment. If that occurs, expiry can leave verified successful void evidence unable to transition the payment from expired to cancelled. The potential effect is confined to synthetic payment state and operation convergence; no ledger corruption or cross-tenant effect is established.

Blockers:

- The decisive PostgreSQL snapshot-to-lock interleaving and its effect with SKIP LOCKED have not been observed. Source alone does not establish that the actual query can retain the stale void-absence result.
- The prior delegated runtime validation was rejected by the provider safety filter before execution. This assignment prohibits further execution or sandbox requests, so no local reproduction was performed.

Safe validation:

- local: An owner may use an isolated PostgreSQL fixture with a dummy scoped authorised payment whose hold has lapsed. Establish the expiry statement snapshot, arrange for void initiation to commit under the payment lock before expiry attempts that lock, and observe whether expiry selects and expires the payment despite the committed PROCESSING void. If it does, apply locally constructed verified success evidence through the native finalizer and inspect the transaction outcome. Also check a void still holding the lock and a void committed before the expiry snapshot. Use no provider or shared service.

Source trace:

- `apps/api/routes/payments_v2.py:115` — The merchant void endpoint requires payments:write and passes the principal's organisation and environment to void initiation.
- `packages/relaypay/payments/service.py:672` — The service locks the scoped payment and checks for a succeeded authorization, no capture and authorised payment status; it does not check whether the hold has lapsed.
- `packages/relaypay/payments/service.py:742` — The service inserts a PROCESSING void and operation within the transaction without changing the payment status or version.
- `packages/relaypay/payments/expiry.py:29` — The expiry statement excludes non-failed voids and selects payment rows FOR UPDATE SKIP LOCKED; the subsequent loop does not separately recheck void rows.
- `packages/relaypay/payments/transitions.py:129` — The transition helper rereads payment status and version and permits authorised-to-expired, without checking void rows.
- `packages/relaypay/provider_operations/finalizer.py:202` — Verified successful void evidence requests a transition to cancelled. If the payment is already expired, the transition is rejected and the finalization transaction rolls back.


## Portfolio metrics may expose tenant-derived values to a broader reader

Fingerprint: `portfolio-metrics-global-tenant-values`

An authorized portfolio request writes its organisation and environment's analytics into shared process gauges. A lower-trust reader who can reach the enabled metrics endpoint and attribute a refresh to that scope could observe those values.

Blockers:

- The effective deployment configuration and network or scraper access policy are unknown. Source does not establish that a lower-trust reader can reach an enabled metrics endpoint and attribute a value to a particular tenant and environment.

Safe validation:

- local: In an isolated bounded fixture with dummy data for two scopes, compare authorized portfolio results with same-process metrics scrapes after each refresh; do not infer external accessibility.
- deployment: An owner should inspect the effective observability setting, API bindings, routing, firewall and scraper permissions, and determine whether a less-trusted reader can observe and attribute a tenant refresh without accessing sensitive values.

Source trace:

- `apps/api/routes/admin.py:2434` — The portfolio endpoint resolves an active environment in the principal's organisation with technical:read or admin scope, then refreshes metrics.
- `packages/relaypay/analytics/service.py:531` — The selected scope is recalculated and its values are written to shared gauges.
- `apps/api/main.py:296` — When observability is enabled, the endpoint refreshes operational gauges and renders the shared registry without a principal dependency.


## PSP callback reads the request body before authentication

Fingerprint: `psp-webhook-unbounded-body`

A client able to reach the PSP callback can submit a body that the API reads in full before checking the provider or its signature. If effective ingress and concurrency limits do not sufficiently bound these requests, concurrent requests could consume memory shared with other API users. The source does not establish an actual deployment route, effective limits, or a meaningful shared availability effect.

Blockers:

- The active route to the API, effective request-size and concurrency limits across ingress and server layers, shared process topology, and recovery behavior are not established by the sanitized source. These facts are decisive for meaningful shared impact.

Safe validation:

- deployment: An owner should inspect the actual PSP route and effective streaming and chunked body-size limits, concurrency limits, API worker sharing and process recovery configuration. Determine whether the combined limits can materially affect other API users without probing a live service.

Source trace:

- `apps/api/routes/inbound_psp.py:16` — The POST /api/inbound/v1/psp/{provider} route accepts a request without a provider authentication dependency.
- `apps/api/routes/inbound_psp.py:18` — The route reads the complete request body before passing it to provider validation and signature verification.


## A snapshot missing its category can produce a non-escalated risk review

Fingerprint: `relaypay/risk_review/checks:absent-category-fails-open`

The snapshot response schema accepts an arbitrary snapshot object. If a clean snapshot lacks category, the category checks assign CRITICAL severity but no hard-stop classification, and contribute 20 risk points when its catalogue is present. With no other points or hard stops, the total is LOW and the review is completed. The shipped site service supplies a category in every fixture, so source does not establish that a lower-trust party can produce this input or that a configured service returns it. No merchant activation or payment bypass is established.

Blockers:

- The source-supported site service contains only immutable synthetic fixtures, all with string categories. It does not establish that a lower-trust party can omit category or influence a configured replacement service.
- The repository does not establish whether any actual configured snapshot service can return an omitted or non-string category, or whether such a response represents an actionable merchant risk decision.

Safe validation:

- local: In the approved isolated sandbox, use a private dummy database and a fake SiteSnapshotSource returning a clean synthetic snapshot with category removed and a valid empty model result. Run prepare_review and execute_review once, then inspect the dummy review status, score and findings. Compare with the same snapshot using a non-HOME_GOODS string. This tests the conditional behavior, not exposure through the shipped fixture service.
- deployment: Without contacting a service from this review, ask the owner to inspect the configured source contract and data provenance: whether omitted or non-string category values are possible, whether a lower-trust party can influence them, and whether completed reviews affect an actual authorization or operational decision.

Source trace:

- `packages/relaypay/risk_review/snapshot.py:50` — The configured site service supplies an HTTP response whose snapshot object is accepted without a category-specific schema.
- `packages/relaypay/risk_review/service.py:111` — The fetched snapshot is stored and passed into the prepared review.
- `packages/relaypay/risk_review/service.py:246` — Deterministic checks run on the prepared snapshot; hard-stop eligibility is determined from classifications.
- `packages/relaypay/risk_review/checks.py:217` — An absent category yields classification NONE, CRITICAL check severity and zero earned category points.
- `packages/relaypay/risk_review/service.py:356` — A review without a hard stop or HIGH or CRITICAL total band is marked COMPLETED.


## Empty or short registrant names can pass the identity consistency check

Fingerprint: `relaypay/risk_review/checks:identity-consistency-empty-or-substring-match`

A configured HTTP snapshot source can return a snapshot whose nonempty merchant name and empty registrant organization pass the deterministic identity consistency check. A short registrant organization also passes when it occurs within the merchant name. This suppresses that check's IMPERSONATION hard stop, although other checks or model findings can still cause escalation. Source does not establish that a lower-trust party can influence those fields in a configured service.

Blockers:

- Source does not establish whether a configured snapshot service can return empty or short registrant organizations under a lower-trust party's influence. The shipped service returns fixed synthetic fixtures.

Safe validation:

- deployment: Without probing a deployed service, ask the owner to inspect the configured snapshot source and its documented data flow for a synthetic onboarding: determine whether a lower-trust party can cause whois.registrantOrganization to be empty or a short substring of identity.merchantName and whether that value reaches the review unchanged. No production or shared data mutation is required.

Source trace:

- `packages/relaypay/risk_review/snapshot.py:50` — The configured HTTP source fetches and validates a response containing a snapshot object without field-level validation of its contents.
- `packages/relaypay/risk_review/service.py:111` — The fetched snapshot is stored for the organisation and environment and supplied to the prepared review.
- `packages/relaypay/risk_review/service.py:246` — The review runs deterministic checks over the prepared snapshot.
- `packages/relaypay/risk_review/checks.py:192` — For a nonempty merchant name, an empty registrant organization satisfies substring containment and produces a NONE identity-consistency classification rather than IMPERSONATION.


## Pricing outlier scoring relies on the snapshot-provided median

Fingerprint: `relaypay/risk_review/checks:pricing-outlier-snapshot-median-trust`

The pricing check uses medianPricePaise from the fetched snapshot. If it is absent or non-positive, the check reports no extreme outlier and awards all 10 available points; a sufficiently high supplied median can also prevent an outlier from being counted. This can reduce the calculated risk contribution by up to 10 points. The shipped synthetic snapshot service supplies fixed values, so source does not establish that a lower-trust party can cause either condition in a configured environment.

Blockers:

- Source does not establish whether any configured HTTP snapshot service permits a lower-trust party to omit or manipulate the median independently of the catalogue prices.

Safe validation:

- local: In an approved isolated sandbox, pass dummy catalogue prices containing a clear outlier to check_pricing with a missing median and with a high supplied median; compare the classifications and earned points with a valid reference median. This verifies the conditional behavior but does not establish external reachability.
- deployment: An owner should inspect the configured snapshot provider's contract and data provenance, without sending live requests, to determine whether a lower-trust party can control or omit the median independently of item prices. If so, assess whether a review's score affects an actual decision.

Source trace:

- `packages/relaypay/risk_review/snapshot.py:50` — The configured HTTP snapshot service returns a response whose snapshot object is accepted without a catalogue-specific schema.
- `packages/relaypay/risk_review/service.py:111` — The fetched snapshot is stored and subsequently supplied to the review.
- `packages/relaypay/risk_review/checks.py:296` — The check reads medianPricePaise from the catalogue and counts outliers only when it is a positive integer.
- `packages/relaypay/risk_review/checks.py:312` — A zero outlier count earns all 10 points, including when no usable median was supplied.


## Unverified model source path can suppress a finding’s hard stop

Fingerprint: `relaypay/risk_review/service:model-source-path-sets-hard-stop-eligibility`

A structured provider can return a verbatim claim quote labeled html. If its classification is not independently corroborated by a deterministic check, the service assigns LOW confidence and suppresses that finding’s hard stop. Other hard stops or a HIGH or CRITICAL total can still escalate the review. The shipped route defaults to a deterministic fake provider; source does not establish a reachable attacker-controlled provider or a real provider that emits this relabeling. No payment or merchant-activation bypass is established.

Blockers:

- Source does not establish that a configured findings provider can be influenced by a lower-trust party or emits html for a claim-derived quote. The shipped fake attributes findings to their top-level snapshot key.

Safe validation:

- deployment: An owner can inspect the actual provider wiring and sanitized synthetic onboarding outputs to determine whether claim text is lower-trust and whether the provider can misattribute its source. Do not probe a live provider.

Source trace:

- `packages/relaypay/risk_review/service.py:225` — The service receives structured output from the findings provider for a delimited snapshot.
- `packages/relaypay/risk_review/findings.py:71` — Validation requires each quote to occur in the redacted snapshot but does not bind it to sourcePath.
- `packages/relaypay/risk_review/service.py:270` — An uncorroborated html-labeled finding receives LOW confidence and cannot set a hard stop; final escalation also depends on other findings and total severity.
