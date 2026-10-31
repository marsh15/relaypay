import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { SourceTextModule, SyntheticModule, createContext } from 'node:vm';

async function workload(responses) {
  const calls = [];
  const rates = [];
  const context = createContext({
    __ENV: { LOAD_RUN_ID: 'run-1' }, __VU: 1, __ITER: 0,
    open: () => JSON.stringify({ apiKey: 'synthetic-test-key', customerId: 'cus_test' }),
  });
  const modules = {
    'k6/http': new SyntheticModule(['default'], function () {
      this.setExport('default', {
        post(url, rawBody, params) {
          calls.push({ url, body: JSON.parse(rawBody), params });
          const reply = responses[calls.length - 1];
          assert.ok(reply, 'unexpected extra mutation');
          return { status: reply.status, json: () => reply.body, timings: { duration: 12 } };
        },
      });
    }, { context }),
    k6: new SyntheticModule(['check', 'sleep'], function () {
      this.setExport('check', (response, checks) => Object.values(checks).every(c => c(response)));
      this.setExport('sleep', () => {});
    }, { context }),
    'k6/metrics': new SyntheticModule(['Rate', 'Trend'], function () {
      this.setExport('Rate', class { add(value) { rates.push(value); } });
      this.setExport('Trend', class { add() {} });
    }, { context }),
  };
  const source = await readFile(new URL('./common.js', import.meta.url), 'utf8');
  const module = new SourceTextModule(source, { context });
  await module.link(name => modules[name]);
  await module.evaluate();
  return { exports: module.namespace, calls, rates, context };
}

const created = { status: 201, body: { id: 'pi_test' } };
const succeeded = { status: 200, body: { status: 'SUCCEEDED' } };

test('refund journey completes prerequisites once with distinct per-command keys', async () => {
  const run = await workload([created, succeeded, succeeded, succeeded]);
  run.exports.journey('refund');
  assert.deepEqual(run.calls.map(c => c.params.tags.name), [
    'create_intent', 'authorize', 'capture', 'refund',
  ]);
  assert.equal(new Set(run.calls.map(c => c.params.headers['Idempotency-Key'])).size, 4);
  assert.equal(run.calls[3].body.amount, 10000);
  assert.deepEqual(run.rates, [true]);
});

test('unresolved authorization stops the journey without resending the mutation', async () => {
  const run = await workload([created, { status: 202, body: { status: 'PROCESSING' } }]);
  run.exports.journey('refund');
  assert.equal(run.calls.length, 2);
  assert.deepEqual(run.rates, [false]);
});

test('HTTP 200 with a failed operation still fails the journey', async () => {
  const run = await workload([created, { status: 200, body: { status: 'FAILED' } }]);
  run.exports.journey('capture');
  assert.equal(run.calls.length, 2);
  assert.deepEqual(run.rates, [false]);
});

test('virtual users and iterations have independent idempotency keys', async () => {
  const run = await workload([created, created, created]);
  run.exports.journey('create_intent');
  run.context.__VU = 2;
  run.exports.journey('create_intent');
  run.context.__ITER = 1;
  run.exports.journey('create_intent');
  assert.equal(new Set(run.calls.map(c => c.params.headers['Idempotency-Key'])).size, 3);
});

test('threshold failures are retained in the saved summary', async () => {
  const run = await workload([]);
  const data = { metrics: { journey_success: { values: { rate: 0 }, thresholds: { ok: false } } } };
  const output = run.exports.handleSummary(data);
  const summary = JSON.parse(output['/results/summary.json']);
  assert.equal(summary.results.metrics.journey_success.thresholds.ok, false);
  assert.equal(summary.workload.limiterImplemented, false);
});
