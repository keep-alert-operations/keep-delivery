import http from 'k6/http';
import { check, sleep } from 'k6';
import exec from 'k6/execution';
import { Rate, Trend } from 'k6/metrics';
import { dashboardResult, requireThat, seconds } from './lib/workload.mjs';

const config = JSON.parse(open(__ENV.CONFIG || '/config/profile.json'));
const accounts = JSON.parse(open(__ENV.SESSIONS_FILE || '/auth/sessions.json'));
const origin = __ENV.API_ORIGIN;
const tenant = __ENV.TENANT_ID;
const dashboards = (__ENV.DASHBOARD_IDS || '').split(',');
const runId = __ENV.RUN_ID;
const uuid = /^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/i;
requireThat(/^https:\/\/[^/@?#\s]+$/.test(origin || ''), 'API_ORIGIN must be the HTTPS browser origin, without trailing slash');
requireThat(uuid.test(tenant || '') && dashboards.every((id) => uuid.test(id)), 'Provide actual tenant and dashboard UUIDs');
requireThat(/^[A-Za-z0-9][A-Za-z0-9-]{0,39}$/.test(runId || ''), 'RUN_ID required');
requireThat(Number.isInteger(config.vus) && config.vus > 0 && config.vus <= 100, 'Read vus: 1-100');
requireThat(Number.isInteger(config.expectedPanels) && config.expectedPanels >= 1 && config.expectedPanels <= 24, 'expectedPanels: 1-24');
requireThat(config.pauseSeconds >= 0.25 && config.pauseSeconds <= 60, 'pauseSeconds: 0.25-60');
requireThat(seconds(config.duration) <= 3600, 'Read chunks limited to one hour: repeat with fresh normal logins');
requireThat(typeof config.searchCEL === 'string' && config.searchCEL.length > 0, 'Supply an actual searchCEL expression');
requireThat(Array.isArray(accounts) && accounts.length === config.vus, 'Exactly one signed-in account/session per VU required');
requireThat(accounts.every((a) => uuid.test(a.principal_id) && /^[A-Za-z0-9_-]{20,128}$/.test(a.cookie)), 'Use principal_id and __Host-appchi_session value from normal OIDC sessions');
requireThat(new Set(accounts.map((a) => a.principal_id)).size === config.vus && new Set(accounts.map((a) => a.cookie)).size === config.vus, 'Use distinct test accounts and sessions');

const readOK = new Rate('appchi_read_ok');
const countMs = new Trend('appchi_dashboard_ms', true);
const searchMs = new Trend('appchi_search_ms', true);
const observations = new Rate('appchi_dashboard_observation_valid');
export const options = {
  scenarios: {readers: {executor: 'constant-vus', vus: config.vus, duration: config.duration, gracefulStop: '15s'}},
  insecureSkipTLSVerify: false, noCookiesReset: true,
  summaryTrendStats: ['avg', 'min', 'med', 'max', 'p(95)', 'p(99)'],
  systemTags: ['status', 'method', 'name', 'scenario', 'expected_response'],
  thresholds: {appchi_read_ok: ['rate==1'], appchi_dashboard_observation_valid: ['rate==1'],
    appchi_dashboard_ms: ['p(99)<2000'], appchi_search_ms: ['p(99)<2000'], checks: ['rate==1']},
};

let csrf = null, expires = 0, refreshed = 0, loaded = null, revisions = null;

function request(method, path, body = null, name = 'setup') {
  return http.request(method, origin + path, body === null ? null : JSON.stringify(body), {
    headers: {'Content-Type': 'application/json', Origin: origin, ...(csrf ? {'X-CSRF-Token': csrf} : {})},
    redirects: 0, timeout: '12s', tags: {name},
  });
}

function checkedJSON(response, description) {
  if (response.status !== 200) exec.test.abort(`${description} failed (HTTP ${response.status}); inspect private service evidence. Response bodies are not logged.`);
  try { return response.json(); } catch (_) { exec.test.abort(`${description}: invalid JSON`); }
}

function ensureRuntime(condition, description) {
  if (!condition) exec.test.abort(description);
}

function session() {
  const now = Date.now();
  if (csrf && now - refreshed < 60000) return;
  const account = accounts[exec.vu.idInTest - 1];
  http.cookieJar().set(origin, '__Host-appchi_session', account.cookie, {path: '/', secure: true});
  const data = checkedJSON(request('GET', '/auth/session', null, 'session_refresh'), 'Normal session refresh').data;
  ensureRuntime(data?.principal?.id === account.principal_id && typeof data.csrf_token === 'string' && data.csrf_token.length > 0, 'Session principal/CSRF mismatch');
  csrf = data.csrf_token;
  expires = Date.parse(data.absolute_at);
  ensureRuntime(Number.isFinite(expires), 'Session absolute expiry is invalid');
  if (!loaded && expires < now + (seconds(config.duration) + 90) * 1000) {
    exec.test.abort('Session will expire during this read chunk; sign in normally and mount fresh sessions.');
  }
  if (expires < now + 60000) exec.test.abort('Session is near absolute expiry; fresh normal OIDC sign-in is required.');
  // This explicitly models active users. Ordinary reads do not bypass idle expiry.
  const activity = checkedJSON(request('POST', '/auth/activity', {}, 'user_activity'), 'Active-user session activity');
  ensureRuntime(activity?.data?.accepted === true, 'Session activity was not accepted');
  refreshed = now;
}

export default function () {
  session();
  const base = `/api/tenants/${tenant}`;
  const id = dashboards[(exec.vu.idInTest - 1) % dashboards.length];
  if (!loaded) {
    loaded = checkedJSON(request('GET', `${base}/dashboards/${id}`), 'Dashboard fixture').data;
    ensureRuntime(Array.isArray(loaded?.panels) && loaded.panels.length === config.expectedPanels && /^\d+$/.test(String(loaded.revision)), 'Dashboard fixture does not match the panel/revision contract');
    revisions = {};
    for (const preset of new Set(loaded.panels.map((p) => p.preset_id))) {
      const definition = checkedJSON(request('GET', `${base}/presets/${preset}`), 'Preset fixture').data;
      ensureRuntime(definition && /^\d+$/.test(String(definition.revision)), 'Preset revision is invalid');
      revisions[preset] = String(definition.revision);
    }
  }
  const count = request('POST', `${base}/dashboards/${id}/counts`, {expected_revision: String(loaded.revision)}, 'dashboard_counts');
  countMs.add(count.timings.duration);
  let body = null;
  try { body = count.json(); } catch (_) { /* counted below */ }
  const valid = count.status === 200 && dashboardResult(body, loaded, revisions);
  observations.add(valid);
  readOK.add(valid);
  check(count, {'dashboard revision, panel set and repeated-preset counts agree': () => valid});
  if (count.status === 401 || count.status === 403) exec.test.abort('Session/access failed; no login bypass or silent retry.');

  const search = request('GET', `${base}/alerts?cel=${encodeURIComponent(config.searchCEL)}&limit=50`, null, 'alert_search');
  searchMs.add(search.timings.duration);
  let feed = null;
  try { feed = search.json(); } catch (_) { /* counted below */ }
  const searchOK = search.status === 200 && feed && Array.isArray(feed.data) && !!feed.meta?.evaluated_at;
  readOK.add(!!searchOK);
  check(search, {'search returns an evaluated alert page': () => !!searchOK});
  if (search.status === 401 || search.status === 403) exec.test.abort('Session/access failed; obtain fresh normal OIDC sessions.');
  sleep(config.pauseSeconds);
}

export function handleSummary(data) {
  const output = __ENV.RESULT_PATH || `/results/${runId}-reads.json`;
  return {
    [output]: JSON.stringify({schema_version: 1, run_id: runId, kind: 'reads', profile: config,
      scope: 'Closed-loop protocol reads with active sessions; no browser/SSE/render, exact-count oracle or PIT instrumentation',
      generated_at: new Date().toISOString(), k6: data}, null, 2),
    stdout: `Run ${runId} read protocol finished; sanitized JSON: ${output}. Check thresholds and browser evidence separately.\n`,
  };
}
