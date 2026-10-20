import http from 'k6/http';
import { check, sleep } from 'k6';
import exec from 'k6/execution';
import { requireThat } from './lib/workload.mjs';

const config = JSON.parse(open(__ENV.CONFIG || '/config/profile.json'));
const account = JSON.parse(open(__ENV.SESSIONS_FILE || '/auth/sessions.json'))[0];
const origin = __ENV.API_ORIGIN;
const tenant = __ENV.TENANT_ID;
const runId = __ENV.RUN_ID;
requireThat(/^https:\/\/[^/@?#\s]+$/.test(origin || ''), 'API_ORIGIN must be the HTTPS browser origin');
requireThat(/^[A-Za-z0-9][A-Za-z0-9-]{0,39}$/.test(runId || ''), 'RUN_ID required');
requireThat(/^[a-f0-9-]{36}$/i.test(tenant || ''), 'Real tenant UUID required');
requireThat(account && /^[A-Za-z0-9_-]{20,128}$/.test(account.cookie), 'A normal OIDC session is required');
export const options = {scenarios: {verification: {
  executor: 'shared-iterations', vus: 1, iterations: 1, maxDuration: '11m',
}}, insecureSkipTLSVerify: false,
  thresholds: {checks: ['rate==1']}, systemTags: ['status', 'method', 'name']};

export default function () {
  http.cookieJar().set(origin, '__Host-appchi_session', account.cookie, {path: '/', secure: true});
  const query = encodeURIComponent(`application == "load-${runId}"`);
  const until = Date.now() + 10 * 60 * 1000;
  while (Date.now() < until) {
    const result = http.get(`${origin}/api/tenants/${tenant}/alerts?cel=${query}&limit=1`, {
      redirects: 0, timeout: '12s', tags: {name: 'seed_search_visibility'},
    });
    if (result.status === 401 || result.status === 403) exec.test.abort('Seed search session/access failed; obtain normal login and correct tenant grant.');
    let body = null;
    try { body = result.json(); } catch (_) { /* retry a not-yet-ready projection only */ }
    if (result.status === 200 && body?.meta?.total === String(config.seedCount) && body.meta.updating === false) {
      check(body, {'exact run-specific seed count is searchable without pending projection': () => true});
      return;
    }
    sleep(2);
  }
  check(false, {'exact run-specific seed count is searchable without pending projection': () => false});
  exec.test.abort('Seed search did not reach the exact expected count in ten minutes; inspect processor/search evidence.');
}

export function handleSummary(data) {
  const path = __ENV.RESULT_PATH || `/results/${runId}-seed-visibility.json`;
  return {[path]: JSON.stringify({schema_version: 1, run_id: runId, kind: 'seed_search_visibility',
    expected_seed_count: config.seedCount, scope: 'Exact search projection count; inspect authoritative SQL and replay proof separately',
    generated_at: new Date().toISOString(), k6: data}, null, 2), stdout: `Seed visibility result: ${path}\n`};
}
