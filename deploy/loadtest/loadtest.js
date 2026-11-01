// k6 load test for the public sandbox, run against the local prod profile (make loadtest) or,
// with BASE_URL and no hosts override, against a staging stack.
//
// Scenarios (SCENARIOS=rate_limit,sse_fanout picks; both by default):
//   rate_limit  one simulated viewer IP sends PER_IP_PER_HOUR + 1 turns: the first ones are
//               accepted (202), the last one is refused with 429 and a Retry-After.
//   sse_fanout  VIEWERS virtual users, each its own simulated IP, start one turn each at the
//               same moment and follow it over SSE to turn_end. turn_latency_ms is POST to
//               turn_end as the viewer saw it.
// The simulated IP travels in X-Harnesswalk-Viewer-Ip, the header the distribution's
// viewer-ip function sets and Caddy turns into the one X-Forwarded-For hop the API trusts.
import http from 'k6/http';
import { check } from 'k6';
import { Counter, Rate, Trend } from 'k6/metrics';

const BASE = __ENV.BASE_URL || 'https://origin.harnesswalk.vynci.dev:8443';
const VERIFY = __ENV.ORIGIN_VERIFY || '';
const VIEWERS = parseInt(__ENV.VIEWERS || '50', 10);
const LIMIT = parseInt(__ENV.PER_IP_PER_HOUR || '30', 10);
const MODEL = __ENV.LOADTEST_MODEL || ''; // fake-small once the public allowlist is in
const SCENARIOS = (__ENV.SCENARIOS || 'rate_limit,sse_fanout').split(',');
const OUT = __ENV.LOADTEST_OUT || '.';

const turnLatency = new Trend('turn_latency_ms', true);
const streamWait = new Trend('sse_time_to_headers_ms', true);
const streamEvents = new Trend('sse_events_per_turn');
const streamCompleted = new Rate('sse_turn_completed');
const fanoutLimited = new Counter('sse_fanout_rate_limited');

const all = {
  rate_limit: { executor: 'per-vu-iterations', vus: 1, iterations: 1, exec: 'rateLimit', maxDuration: '2m' },
  sse_fanout: {
    executor: 'per-vu-iterations', vus: VIEWERS, iterations: 1, exec: 'sseViewer', maxDuration: '3m',
    // After the rate-limit probe when both run, so they never share a moment.
    startTime: SCENARIOS.includes('rate_limit') ? '20s' : '0s',
  },
};
const thresholds = {};
if (SCENARIOS.includes('rate_limit')) {
  thresholds['checks{check:first turns accepted}'] = ['rate==1'];
  thresholds['checks{check:one over the limit is 429}'] = ['rate==1'];
  thresholds['checks{check:429 carries Retry-After}'] = ['rate==1'];
}
if (SCENARIOS.includes('sse_fanout')) {
  thresholds.sse_turn_completed = ['rate>=0.98'];
  thresholds.turn_latency_ms = ['p(95)<10000'];
}

export const options = {
  scenarios: Object.fromEntries(SCENARIOS.map((s) => [s, all[s]])),
  thresholds,
  hosts: __ENV.BASE_URL ? {} : { 'origin.harnesswalk.vynci.dev': '127.0.0.1' },
  insecureSkipTLSVerify: !__ENV.BASE_URL,
  summaryTrendStats: ['avg', 'min', 'med', 'p(90)', 'p(95)', 'max'],
};

const headers = (ip, extra) =>
  Object.assign(
    { 'Content-Type': 'application/json', 'X-Harnesswalk-Viewer-Ip': ip },
    VERIFY ? { 'X-Origin-Verify': VERIFY } : {},
    extra || {},
  );
const turnBody = (message) => JSON.stringify(MODEL ? { message, model: MODEL } : { message });

export function rateLimit() {
  const ip = '203.0.113.7';
  const codes = [];
  let last;
  for (let i = 0; i <= LIMIT; i += 1) {
    http.cookieJar().clear(BASE); // a fresh tenant each time: this is the per-IP limit
    last = http.post(`${BASE}/v1/turns`, turnBody(`rate limit probe ${i}`), {
      headers: headers(ip),
      tags: { name: 'POST /v1/turns (rate limit)' },
    });
    codes.push(last.status);
  }
  check(codes, { 'first turns accepted': (c) => c.slice(0, LIMIT).every((s) => s === 202) });
  check(last, {
    'one over the limit is 429': (r) => r.status === 429,
    '429 carries Retry-After': (r) => r.status === 429 && Number(r.headers['Retry-After']) > 0,
  });
  const counts = codes.reduce((m, s) => ((m[s] = (m[s] || 0) + 1), m), {});
  console.log(`rate_limit: ${JSON.stringify(counts)}; turn ${LIMIT + 1} got ${last.status}` +
    ` Retry-After=${last.headers['Retry-After'] || '-'}`);
}

export function sseViewer() {
  const ip = `198.51.100.${(__VU % 250) + 1}`;
  const t0 = Date.now();
  const start = http.post(`${BASE}/v1/turns`, turnBody('What are your support hours?'), {
    headers: headers(ip),
    tags: { name: 'POST /v1/turns (viewer)' },
  });
  if (start.status === 429) {
    fanoutLimited.add(1);
    streamCompleted.add(false);
    return;
  }
  check(start, { 'viewer turn accepted': (r) => r.status === 202 });
  if (start.status !== 202) {
    streamCompleted.add(false);
    return;
  }
  const turn = start.json('turn_id');
  const stream = http.get(`${BASE}/v1/turns/${turn}/events`, {
    headers: headers(ip, { Accept: 'text/event-stream' }),
    timeout: '90s',
    tags: { name: 'GET /v1/turns/{id}/events (SSE)' },
  });
  const body = stream.body || '';
  const done = body.includes('event: turn_end');
  streamCompleted.add(done);
  streamWait.add(stream.timings.waiting);
  streamEvents.add((body.match(/^event: /gm) || []).length);
  if (done) turnLatency.add(Date.now() - t0);
  check(stream, { 'stream ends with turn_end': () => done });
}

const fmt = (m, keys) =>
  m ? keys.map((k) => `${k}=${Math.round(m.values[k] * 10) / 10}`).join(' ') : 'n/a';

export function handleSummary(data) {
  const m = data.metrics;
  const lines = [
    `scenarios: ${SCENARIOS.join(', ')}  viewers: ${VIEWERS}  per-IP limit: ${LIMIT}`,
    `http_reqs: ${m.http_reqs ? m.http_reqs.values.count : 0}  checks passed: ${m.checks ? Math.round(m.checks.values.rate * 1000) / 10 : 'n/a'}%`,
  ];
  if (SCENARIOS.includes('sse_fanout')) {
    lines.push(
      `sse_turn_completed: ${m.sse_turn_completed ? Math.round(m.sse_turn_completed.values.rate * 1000) / 10 : 'n/a'}%` +
        `  rate-limited viewers: ${m.sse_fanout_rate_limited ? m.sse_fanout_rate_limited.values.count : 0}`,
      `turn_latency_ms: ${fmt(m.turn_latency_ms, ['min', 'med', 'p(95)', 'max'])}`,
      `sse_time_to_headers_ms: ${fmt(m.sse_time_to_headers_ms, ['med', 'p(95)', 'max'])}`,
      `sse_events_per_turn: ${fmt(m.sse_events_per_turn, ['min', 'med', 'max'])}`,
    );
  }
  const failed = Object.entries(data.metrics)
    .filter(([, v]) => v.thresholds && Object.values(v.thresholds).some((t) => !t.ok))
    .map(([k]) => k);
  lines.push(`thresholds: ${failed.length ? `FAILED ${failed.join(', ')}` : 'all passed'}`);
  return { stdout: `${lines.join('\n')}\n`, [`${OUT}/k6-summary.json`]: JSON.stringify(data, null, 2) };
}
