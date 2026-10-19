import { sleep } from 'k6';
import exec from 'k6/execution';
import { envelope, payload } from './lib/workload.mjs';
import { config, runId, post, basicOptions, summary } from './lib/runtime.js';

export const options = {...basicOptions, vus: 1, iterations: 1};

export default function () {
  // Distinct keys in each batch: the admission service publishes members concurrently.
  const batchSize = config.memberBytes ? Math.min(100, Math.floor((4194304 - 12) / (config.memberBytes + 13))) : 100;
  for (let first = 0; first < config.seedCount; first += batchSize) {
    const items = [];
    for (let i = first; i < Math.min(first + batchSize, config.seedCount); i++) {
      items.push(payload(config, runId, `seed-${i}`, 'seed'));
    }
    if (!post(envelope(items, true), items.length)) {
      exec.test.abort('Seed publication failed or was uncertain; reconcile evidence before a fresh run. No automatic retries.');
    }
    sleep(0.25);
  }
}

export function handleSummary(data) { return summary(data, 'seed'); }
