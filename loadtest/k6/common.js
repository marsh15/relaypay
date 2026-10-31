import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate, Trend } from 'k6/metrics';

const journeySuccess = new Rate('journey_success');
const commandLatency = new Trend('command_latency', true);
const credentials = JSON.parse(open(__ENV.LOAD_CREDENTIALS || '/run/load-credentials.json'));
const baseUrl = (__ENV.LOAD_BASE_URL || 'http://api:8000').replace(/\/$/, '');
const runId = __ENV.LOAD_RUN_ID;
if (!runId) throw new Error('LOAD_RUN_ID is required to keep keys distinct across runs');

export const options = {
  vus: Number(__ENV.LOAD_VUS || 4),
  duration: __ENV.LOAD_DURATION || '30s',
  summaryTrendStats: ['min', 'med', 'p(95)', 'p(99)', 'max'],
  thresholds: {
    http_req_failed: ['rate<0.01'],
    journey_success: ['rate>0.99'],
    command_latency: ['p(95)<2000'],
    'command_latency{command:create_intent}': ['p(95)<2000'],
  },
  systemTags: ['status', 'method', 'name', 'scenario', 'expected_response'],
};

const workload = __ENV.LOAD_SCRIPT || 'mixed';
if (workload !== 'create_intent') options.thresholds['command_latency{command:authorize}'] = ['p(95)<2000'];
if (!['create_intent', 'authorize'].includes(workload)) options.thresholds['command_latency{command:capture}'] = ['p(95)<2000'];
if (['refund', 'mixed'].includes(workload)) options.thresholds['command_latency{command:refund}'] = ['p(95)<2000'];

function post(path, body, command, expectedStatus) {
  const response = http.post(`${baseUrl}${path}`, JSON.stringify(body), {
    headers: {
      Authorization: `Bearer ${credentials.apiKey}`,
      'Content-Type': 'application/json',
      'Idempotency-Key': `${runId}:${__VU}:${__ITER}:${command}`,
    },
    tags: { name: command },
    timeout: '10s',
  });
  commandLatency.add(response.timings.duration, { command });
  let payload;
  try { payload = response.json(); } catch (_) { payload = null; }
  const succeeded = check(response, {
    [`${command} expected HTTP status`]: r => r.status === expectedStatus,
    [`${command} completed`]: () => payload !== null &&
      (command === 'create_intent' ? typeof payload.id === 'string' : payload.status === 'SUCCEEDED'),
  });
  return succeeded ? payload : null;
}

export function journey(kind) {
  let success = false;
  try {
    const payment = post('/api/v1/payment_intents', {
      customer_id: credentials.customerId,
      merchant_reference: `${runId}:${__VU}:${__ITER}`,
      amount: 10000,
      currency: 'INR',
    }, 'create_intent', 201);
    if (!payment) return;
    if (kind === 'create_intent') { success = true; return; }
    const path = `/api/v1/payment_intents/${payment.id}`;
    if (!post(`${path}/authorize`, {}, 'authorize', 200)) return;
    if (kind === 'authorize') { success = true; return; }
    if (!post(`${path}/capture`, {}, 'capture', 200)) return;
    if (kind === 'capture') { success = true; return; }
    // Mixed: half captured-only, half fully refunded, deterministic across iterations.
    if (kind === 'mixed' && (__VU + __ITER) % 2 === 0) { success = true; return; }
    success = post(`${path}/refunds`, {
      amount: 10000, currency: 'INR',
      merchant_refund_reference: `${runId}:${__VU}:${__ITER}:refund`,
    }, 'refund', 200) !== null;
  } finally {
    journeySuccess.add(success);
    sleep(0.1);
  }
}

export function handleSummary(data) {
  const payload = {
    schemaVersion: 1,
    workload: {
      runId, script: __ENV.LOAD_SCRIPT || 'mixed',
      limiterLabel: __ENV.LOAD_LIMITER || 'off',
      limiterImplemented: false,
      vus: options.vus, duration: options.duration, thinkTimeSeconds: 0.1,
      paymentAmountPaise: 10000,
      prerequisitesIncludedInHttpMetrics: true,
    },
    results: data,
    claim: 'Synthetic HTTP performance only; financial invariants are checked by the proof suite.',
  };
  return {
    '/results/summary.json': JSON.stringify(payload, null, 2) + '\n',
    stdout: `Results saved: ${runId}; journey success ${data.metrics.journey_success?.values.rate ?? 'no iterations'}\n`,
  };
}
