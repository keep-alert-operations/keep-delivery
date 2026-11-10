// Pure workload contracts: also executed by the Node unit tests.
export function requireThat(condition, message) {
  if (!condition) throw new Error(message);
}

export function seconds(value) {
  const match = /^(\d+)(s|m|h)$/.exec(value);
  requireThat(match && Number(match[1]) > 0, 'duration must be a positive integer with s, m, or h');
  return Number(match[1]) * {s: 1, m: 60, h: 3600}[match[2]];
}

export function validateConfig(config, runId, endpoint) {
  requireThat(/^[A-Za-z0-9][A-Za-z0-9-]{0,39}$/.test(runId || ''), 'RUN_ID: 1-40 letters, digits, hyphens');
  requireThat(['v1', 'v2'].includes(config.version), 'version must be v1 or v2');
  requireThat(Number.isInteger(config.batchSize) && config.batchSize >= 1 && config.batchSize <= 100, 'batchSize: 1-100');
  requireThat(typeof config.itemsPerSecond === 'number' && /^\d+(\.\d)?$/.test(String(config.itemsPerSecond)) && config.itemsPerSecond > 0, 'itemsPerSecond must be positive with at most one decimal place');
  const plannedTenths = Math.round(config.itemsPerSecond * 10) * seconds(config.duration);
  requireThat(plannedTenths % (config.batchSize * 10) === 0, 'duration/rate must offer an exact whole number of batches; extend duration, do not round rates');
  requireThat(Number.isInteger(config.vus) && config.vus >= 1 && config.vus <= 2000, 'vus: 1-2000');
  requireThat(Number.isInteger(config.newPercent) && config.newPercent >= 0 && config.newPercent <= 100, 'newPercent: 0-100');
  requireThat(['bounded', 'growing'].includes(config.lifecycle), 'lifecycle must be bounded or growing');
  requireThat(config.lifecycle !== 'bounded' || config.newPercent <= 50, 'bounded lifecycle needs at least as many updates as new items');
  requireThat(Number.isInteger(config.seedCount) && config.seedCount >= config.vus * config.batchSize, 'seedCount must cover every VU/batch slot');
  requireThat(Number.isInteger(config.messageChars) && config.messageChars >= 100 && config.messageChars <= 8192, 'messageChars: 100-8192');
  requireThat(config.memberBytes === undefined || (Number.isInteger(config.memberBytes) && config.memberBytes >= 1024 && config.memberBytes <= 65536), 'memberBytes: optional 1024-65536 raw UTF-8 bytes');
  requireThat(config.memberBytes === undefined || config.batchSize === 1 || 12 + config.batchSize * (config.memberBytes + 13) <= 4194304, 'Requested member size/batch exceeds the 4 MiB envelope limit');
  requireThat(seconds(config.duration) <= 86400, 'one run is limited to 24 hours');
  requireThat(typeof endpoint === 'string' && new RegExp(`^https://[^/@?#\\s]+/api/ingest/${config.version}/alerts$`).test(endpoint), 'INGEST_URL must be the exact HTTPS public native endpoint, with no credentials/query');
  return config;
}

export function isNew(ordinal, percent) {
  // Balanced deterministic allocation: exact in every full 100-item block.
  return Math.floor((ordinal + 1) * percent / 100) > Math.floor(ordinal * percent / 100);
}

export function arrivalSchedule(config) {
  // Convert the validated decimal to exact integer tenths, then reduce the ratio.
  // 250 items/s, 100/item batch => rate 5 per 2s; 66.7/s single => 667 per 10s.
  const numerator = Math.round(config.itemsPerSecond * 10);
  const denominator = config.batchSize * 10;
  let a = numerator, b = denominator;
  while (b) { const remainder = a % b; a = b; b = remainder; }
  return {rate: numerator / a, timeUnit: `${denominator / a}s`};
}

export function keyFor(config, vu, slot, ordinal) {
  return isNew(ordinal, config.newPercent) ? `new-${ordinal}` : `seed-${(vu - 1) * config.batchSize + slot}`;
}

export function nativeTime(date) {
  const pad = (v) => String(v).padStart(2, '0');
  return `${pad(date.getUTCDate())}-${pad(date.getUTCMonth() + 1)}-${date.getUTCFullYear()} ${pad(date.getUTCHours())}:${pad(date.getUTCMinutes())}:${pad(date.getUTCSeconds())}`;
}

export function payload(config, runId, key, sequence, at = new Date()) {
  const marker = `Keep load ${runId} ${key} sequence=${sequence} `;
  const shared = {
    application: `load-${runId}`,
    node_name: key,
    message: marker.padEnd(config.messageChars, 'x'),
    time_created: nativeTime(at),
  };
  if (config.version === 'v1') return {
    ...shared, object: 'synthetic-load', operator: 'synthetic-load', severity: 'error',
    key_field: `${runId}:${key}`,
  };
  // Only message/time vary on updates: severity, impact and runbook are identity fields.
  return {
    ...shared, component: 'synthetic-load', severity: 'critical', status: 'firing',
    site: 'load-test', impact: 'Synthetic qualification traffic',
    runbook_url: 'https://example.invalid/keep-load-test',
  };
}

export function envelope(items, forceBatch = false) {
  return items.length === 1 && !forceBatch ? items[0] : {alerts: items.map((p) => ({payload: p}))};
}

export function utf8Bytes(text) {
  let count = 0;
  for (const char of text) {
    const point = char.codePointAt(0);
    count += point < 0x80 ? 1 : point < 0x800 ? 2 : point < 0x10000 ? 3 : 4;
  }
  return count;
}

export function encodeRequest(items, memberBytes, forceBatch = false) {
  const normalizedSizes = [], memberSizes = [];
  const members = items.map((item) => {
    let text = JSON.stringify(item);
    const bytes = utf8Bytes(text);
    normalizedSizes.push(bytes);
    requireThat(bytes <= 65536, 'Normalized native member exceeds 64 KiB');
    if (memberBytes !== undefined) {
      requireThat(bytes <= memberBytes, 'Native fields exceed requested memberBytes; increase size or shorten message');
      // Padding is within the object so exact source-object slicing includes it.
      text = text.slice(0, -1) + ' '.repeat(memberBytes - bytes) + '}';
    }
    const actual = utf8Bytes(text);
    requireThat(actual <= 65536, 'Raw native member exceeds 64 KiB');
    memberSizes.push(actual);
    return text;
  });
  const body = members.length === 1 && !forceBatch ? members[0] : '{"alerts":[' + members.map((m) => '{"payload":' + m + '}').join(',') + ']}';
  const bytes = utf8Bytes(body);
  requireThat(bytes <= 4194304, 'Native envelope exceeds 4 MiB');
  return {body, bytes, memberSizes, normalizedSizes};
}

export function ownedSeedKeys(config, vu) {
  const keys = [];
  for (let i = vu - 1; i < config.seedCount; i += config.vus) keys.push(`seed-${i}`);
  return keys;
}

export function boundedBatch(config, runId, vuIteration, globalIteration, queue, at = new Date()) {
  const items = [], retained = [], created = [];
  let newCount = 0, resolveCount = 0;
  for (let slot = 0; slot < config.batchSize; slot++) {
    const localOrdinal = vuIteration * config.batchSize + slot;
    const globalOrdinal = globalIteration * config.batchSize + slot;
    if (isNew(localOrdinal, config.newPercent)) {
      const key = `new-${globalOrdinal}`;
      created.push(key);
      items.push(payload(config, runId, key, globalOrdinal, at));
      newCount++;
    } else {
      requireThat(queue.length > 0, 'Insufficient owned active fixtures for this batch');
      const key = queue.shift();
      const priorUpdates = localOrdinal - Math.floor(localOrdinal * config.newPercent / 100);
      const denominator = 100 - config.newPercent;
      const resolve = Math.floor((priorUpdates + 1) * config.newPercent / denominator) >
        Math.floor(priorUpdates * config.newPercent / denominator);
      const p = payload(config, runId, key, globalOrdinal, at);
      if (resolve) {
        if (config.version === 'v1') p.severity = 'clear';
        else p.status = 'resolved';
        resolveCount++;
      } else retained.push(key);
      items.push(p);
    }
  }
  // Delay reinsertion until all selections finish: never publish the same key twice in a batch.
  queue.push(...retained, ...created);
  return {items, newCount, resolveCount};
}

export function admissionResult(status, body, expected) {
  const result = {valid: false, accepted: 0, rejected: 0, unconfirmed: expected};
  if (!body || !Array.isArray(body.results) || body.results.length !== expected ||
      !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(body.request_id || '')) return result;
  const known = new Set();
  let accepted = 0, rejected = 0, unconfirmed = 0;
  for (let position = 0; position < body.results.length; position++) {
    const item = body.results[position];
    if (item.index !== position || known.has(item.index)) return result;
    known.add(item.index);
    if (item.status === 202 && item.disposition === 'accepted' && typeof item.received_at === 'string' && Number.isFinite(Date.parse(item.received_at))) accepted++;
    else if (item.status === 422 && item.disposition === 'rejected' && item.error) rejected++;
    else if (item.status === 503 && item.disposition === 'publish_failed' && item.error) unconfirmed++;
    else return result;
  }
  const aggregate = accepted === expected ? 202 : rejected === expected ? 422 : accepted > 0 ? 207 : 503;
  return {valid: status === aggregate, accepted, rejected, unconfirmed};
}

export function dashboardResult(body, definition, revisions) {
  const d = body && body.data;
  if (!d || d.dashboard_revision !== String(definition.revision) ||
      !d.observation_id || !d.evaluated_at || !d.watermark ||
      !Array.isArray(d.panels) || d.panels.length !== definition.panels.length) return false;
  if (JSON.stringify(Object.keys(d.preset_revisions || {}).sort()) !== JSON.stringify(Object.keys(revisions).sort())) return false;
  if (Object.keys(revisions).some((key) => d.preset_revisions[key] !== revisions[key])) return false;
  const panels = new Map(definition.panels.map((p) => [p.id, p.preset_id]));
  const counts = new Map();
  for (const p of d.panels) {
    if (panels.get(p.panel_id) !== p.preset_id || !/^\d+$/.test(p.count)) return false;
    if (counts.has(p.preset_id) && counts.get(p.preset_id) !== p.count) return false;
    counts.set(p.preset_id, p.count);
    panels.delete(p.panel_id);
  }
  return panels.size === 0;
}
