import http from 'k6/http';
import { Counter, Rate, Trend } from 'k6/metrics';
import { check } from 'k6';
import exec from 'k6/execution';
import { admissionResult, encodeRequest, requireThat, validateConfig } from './workload.mjs';

export const config = JSON.parse(open(__ENV.CONFIG || '/config/profile.json'));
export const runId = __ENV.RUN_ID;
export const endpoint = __ENV.INGEST_URL;
validateConfig(config, runId, endpoint);
const apiKey = config.version === 'v2' ? open(__ENV.API_KEY_FILE || '/auth/operator-key').trim() : null;
requireThat(config.version !== 'v2' || (apiKey && !/[\r\n]/.test(apiKey)), 'Provide one normal, active operator key via API_KEY_FILE');

export const attempted = new Counter('keep_items_attempted');
export const accepted = new Counter('keep_items_accepted');
export const rejected = new Counter('keep_items_rejected');
export const unconfirmed = new Counter('keep_items_unconfirmed');
export const newItems = new Counter('keep_new_items_attempted');
export const updates = new Counter('keep_updates_attempted');
export const resolutions = new Counter('keep_resolution_updates_attempted');
export const admissionOK = new Rate('keep_admission_ok');
export const admissionMs = new Trend('keep_admission_ms', true);
export const rawMemberBytes = new Trend('keep_raw_member_bytes');
export const normalizedMemberBytes = new Trend('keep_normalized_member_bytes');
export const envelopeBytes = new Trend('keep_envelope_bytes');
export const sentBytes = new Counter('keep_request_body_bytes_attempted');

export function post(body, count) {
  let encoded;
  try {
    encoded = encodeRequest(body.alerts ? body.alerts.map((m) => m.payload) : [body], config.memberBytes, !!body.alerts);
  } catch (_) {
    exec.test.abort('Generated native member/envelope exceeds configured or protocol byte limits; correct profile before sending traffic.');
  }
  for (const bytes of encoded.memberSizes) rawMemberBytes.add(bytes);
  for (const bytes of encoded.normalizedSizes) normalizedMemberBytes.add(bytes);
  envelopeBytes.add(encoded.bytes);
  sentBytes.add(encoded.bytes);
  attempted.add(count);
  const response = http.post(endpoint, encoded.body, {
    headers: {'Content-Type': 'application/json', ...(apiKey ? {'X-API-Key': apiKey} : {})},
    timeout: '5s', redirects: 0, tags: {name: `native_${config.version}`},
  });
  let parsed = null;
  try { parsed = response.json(); } catch (_) { /* counted below; never print a response */ }
  const result = admissionResult(response.status, parsed, count);
  accepted.add(result.accepted);
  rejected.add(result.rejected);
  unconfirmed.add(result.unconfirmed);
  const ok = result.valid && result.accepted === count;
  admissionOK.add(ok);
  admissionMs.add(response.timings.duration);
  check(response, {'all native members acknowledged with valid envelope': () => ok});
  return ok;
}

export const basicOptions = {
  insecureSkipTLSVerify: false,
  discardResponseBodies: false,
  summaryTrendStats: ['avg', 'min', 'med', 'max', 'p(95)', 'p(99)'],
  systemTags: ['status', 'method', 'name', 'scenario', 'expected_response'],
  thresholds: {
    keep_admission_ok: ['rate==1'],
    keep_admission_ms: ['p(99)<1000'],
    checks: ['rate==1'],
  },
};

export function summary(data, kind) {
  const output = __ENV.RESULT_PATH || `/results/${runId}-${kind}.json`;
  return {
    [output]: JSON.stringify({schema_version: 1, run_id: runId, kind, profile: config,
      scope: 'Kafka admission only; 202 is not PostgreSQL application or rendered visibility',
      generated_at: new Date().toISOString(), k6: data}, null, 2),
    stdout: `Run ${runId} (${kind}) finished; sanitized JSON: ${output}. Inspect thresholds and independent pipeline evidence.\n`,
  };
}
