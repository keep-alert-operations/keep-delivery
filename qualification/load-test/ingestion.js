import exec from 'k6/execution';
import { arrivalSchedule, boundedBatch, envelope, isNew, keyFor, ownedSeedKeys, payload, requireThat, seconds } from './lib/workload.mjs';
import { config, runId, post, basicOptions, summary, newItems, updates, resolutions } from './lib/runtime.js';

requireThat(__ENV.FIXTURE_READY === '1', 'Run seed.js, verify PostgreSQL/search application, then set FIXTURE_READY=1');
const plannedItems = Math.round(config.itemsPerSecond * 10) * seconds(config.duration) / 10;

export const options = {
  ...basicOptions,
  scenarios: {admission: {
    executor: 'constant-arrival-rate',
    ...arrivalSchedule(config), duration: config.duration,
    preAllocatedVUs: config.vus, maxVUs: config.vus, gracefulStop: '10s',
  }},
  thresholds: {...basicOptions.thresholds,
    dropped_iterations: ['count==0'],
    appchi_items_attempted: [`count==${plannedItems}`],
    appchi_items_accepted: [`count==${plannedItems}`],
  },
};

let owned = null;

export default function () {
  if (config.lifecycle === 'bounded') {
    if (!owned) owned = ownedSeedKeys(config, exec.vu.idInTest);
    const batch = boundedBatch(config, runId, exec.vu.iterationInScenario, exec.scenario.iterationInTest, owned);
    newItems.add(batch.newCount);
    updates.add(config.batchSize - batch.newCount);
    resolutions.add(batch.resolveCount);
    if (!post(envelope(batch.items), batch.items.length)) {
      exec.test.abort('Bounded lifecycle publication failed or was uncertain; reconcile before a new run. FIFO state cannot safely continue.');
    }
    return;
  }
  const items = [];
  for (let slot = 0; slot < config.batchSize; slot++) {
    const ordinal = exec.scenario.iterationInTest * config.batchSize + slot;
    const key = keyFor(config, exec.vu.idInTest, slot, ordinal);
    items.push(payload(config, runId, key, ordinal));
    if (isNew(ordinal, config.newPercent)) newItems.add(1);
    else updates.add(1);
  }
  // No retry: resubmission is a new source delivery. Uncertainty is a failed run.
  post(envelope(items), items.length);
}

export function handleSummary(data) { return summary(data, 'ingestion'); }
