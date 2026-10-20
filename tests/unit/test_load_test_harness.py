"""Exercise generator identity/order accounting and actual native contracts without traffic."""

import json
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]
TOOLKIT = ROOT / "qualification" / "load-test"
NODE = shutil.which("node")


def run_js(source):
    if not NODE:
        pytest.skip("Node is required for the k6 pure-contract checks")
    completed = subprocess.run(
        [NODE, "--input-type=module", "-e", source],
        cwd=TOOLKIT,
        check=True,
        capture_output=True,
        text=True,
        timeout=120,
    )
    return completed.stdout


def test_profiles_and_rate_validation():
    run_js("""
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {arrivalSchedule, validateConfig} from './lib/workload.mjs';
for (const name of fs.readdirSync('profiles')) {
  const p = JSON.parse(fs.readFileSync('profiles/' + name, 'utf8'));
  if (!p.itemsPerSecond) continue;
  validateConfig(p, 'test-run', 'https://gateway.example/api/ingest/v2/alerts');
  assert.throws(() => validateConfig({...p, itemsPerSecond:10,duration:'1s',batchSize:3}, 'test-run', 'https://gateway.example/api/ingest/v2/alerts'));
  assert.throws(() => validateConfig(p, 'test-run', 'https://secret:password@gateway.example/api/ingest/v2/alerts'));
  assert.throws(() => validateConfig({...p, lifecycle:'bounded', newPercent:80}, 'test-run', 'https://gateway.example/api/ingest/v2/alerts'));
}
assert.deepEqual(arrivalSchedule({itemsPerSecond:250,batchSize:100}),{rate:5,timeUnit:'2s'});
assert.deepEqual(arrivalSchedule({itemsPerSecond:66.7,batchSize:1}),{rate:667,timeUnit:'10s'});
""")


@pytest.mark.parametrize("version", ["v1", "v2"])
def test_payloads_match_actual_native_contract_and_identity(version):
    from appchi.ingestion.native import canonical, identity, validate_native

    bodies = json.loads(run_js("""
import {payload} from './lib/workload.mjs';
const config = {version: VERSION, messageChars:256};
console.log(JSON.stringify([
  payload(config,'unit-run','seed-1',0,new Date('2026-10-05T10:00:00Z')),
  payload(config,'unit-run','seed-1',1,new Date('2026-10-05T10:00:01Z')),
  payload(config,'unit-run','new-2',2,new Date('2026-10-05T10:00:01Z')),
]));
""".replace("VERSION", json.dumps(version))))
    for body in bodies:
        validate_native(body, version, canonical(body))
    operator = "00000000-0000-4000-8000-000000000001"
    assert identity(bodies[0], version, operator) == identity(bodies[1], version, operator)
    assert identity(bodies[0], version, operator) != identity(bodies[2], version, operator)
    assert bodies[0]["message"] != bodies[1]["message"]


def test_owned_keys_and_bounded_lifecycle_remain_ordered_and_balanced():
    run_js("""
import assert from 'node:assert/strict';
import {boundedBatch, ownedSeedKeys, isNew, keyFor} from './lib/workload.mjs';
for (const percentage of [20,50]) {
  for (const batchSize of [1,5,10,100]) {
    const p={version:'v2', messageChars:256, vus:5, seedCount:5000, batchSize, newPercent:percentage};
    const queues = Array.from({length:5},(_,v)=>ownedSeedKeys(p,v+1));
    const ownership = new Map();
    for (let v=0;v<5;v++) for (const key of queues[v]) ownership.set(key,v);
    const active = new Set(ownership.keys());
    const iterations = [0,0,0,0,0];
    let total=0, created=0, resolved=0;
    for (let iteration=0;iteration<1000;iteration++) {
      // Uneven VU scheduling must not route an update to another VU's key.
      const v = iteration%7===0 ? 0 : iteration%5;
      const batch=boundedBatch(p,'run',iterations[v]++,iteration,queues[v]);
      const keys = batch.items.map((a)=>a.node_name);
      assert.equal(new Set(keys).size, keys.length);
      for (const a of batch.items) {
        const key=a.node_name;
        if (key.startsWith('new-') && !ownership.has(key)) {
          ownership.set(key,v); active.add(key);
        } else {
          assert.equal(ownership.get(key),v);
          assert.ok(active.has(key));
          if (a.status==='resolved') active.delete(key);
        }
      }
      total+=batch.items.length; created+=batch.newCount; resolved+=batch.resolveCount;
      assert.ok(Math.abs(active.size-5000)<=p.vus);
    }
    assert.ok(Math.abs(created-total*percentage/100)<p.vus);
    assert.equal(active.size,5000+created-resolved);
  }
}
for (const percentage of [20,50,80]) {
  assert.equal(Array.from({length:100},(_,i)=>isNew(i,percentage)).filter(Boolean).length,percentage);
}
const config={batchSize:10,newPercent:0};
assert.notEqual(keyFor(config,1,0,0),keyFor(config,2,0,0));
assert.notEqual(keyFor(config,1,0,0),keyFor(config,1,1,0));
""")


def test_admission_validates_every_member_and_aggregate():
    run_js("""
import assert from 'node:assert/strict';
import {admissionResult} from './lib/workload.mjs';
const item=(i,status=202)=>({index:i,status,disposition:status===202?'accepted':status===422?'rejected':'publish_failed',received_at:'2026-10-05T10:00:00Z',error:{code:'synthetic'}});
const body={request_id:'00000000-0000-4000-8000-000000000001',results:[item(0),item(1)]};
assert.deepEqual(admissionResult(202,body,2),{valid:true,accepted:2,rejected:0,unconfirmed:0});
assert.equal(admissionResult(202,{...body,request_id:'-'.repeat(36)},2).valid,false);
assert.equal(admissionResult(202,{...body,results:[{...item(0),received_at:0},item(1)]},2).valid,false);
assert.equal(admissionResult(202,{...body,results:[item(0),item(0)]},2).valid,false);
assert.equal(admissionResult(202,{...body,results:[item(1),item(0)]},2).valid,false);
assert.equal(admissionResult(202,{...body,results:[item(0)]},2).valid,false);
assert.deepEqual(admissionResult(207,{...body,results:[item(0),item(1,503)]},2),{valid:true,accepted:1,rejected:0,unconfirmed:1});
assert.equal(admissionResult(202,{...body,results:[item(0),item(1,503)]},2).valid,false);
assert.equal(admissionResult(422,{...body,results:[item(0,422),item(1,422)]},2).valid,true);
assert.equal(admissionResult(503,{...body,results:[item(0,422),item(1,503)]},2).valid,true);
assert.equal(admissionResult(202,null,2).unconfirmed,2);
""")


def test_encoded_byte_limits_preserve_actual_native_contract():
    from appchi.ingestion.kafka_admission import validate_request

    encoded = json.loads(run_js("""
import assert from 'node:assert/strict';
import {encodeRequest,payload,utf8Bytes} from './lib/workload.mjs';
const p=payload({version:'v2',messageChars:3500},'bytes','seed-1',0);
const small=encodeRequest([p],4096);
assert.equal(small.bytes,4096);
assert.ok(small.normalizedSizes[0]>3500 && small.normalizedSizes[0]<4096);
assert.equal(utf8Bytes('aé😀'),7);
const large=encodeRequest([p],65536);
assert.equal(large.memberSizes[0],65536);
assert.throws(()=>encodeRequest([p],1024));
assert.throws(()=>encodeRequest(Array(100).fill(p),65536,true));
const batch=encodeRequest(Array(100).fill(p),41929,true);
assert.ok(batch.bytes<=4194304 && batch.bytes>4194000);
console.log(JSON.stringify([small.body,large.body]));
"""))
    for body in encoded:
        members = validate_request(body.encode(), "v2")
        assert len(members) == 1
        assert members[0][2] is None
        assert len(members[0][1]) in (4096, 65536)


def test_dashboard_detects_repeated_preset_disagreement_and_stale_revisions():
    run_js("""
import assert from 'node:assert/strict';
import {dashboardResult} from './lib/workload.mjs';
const definition={revision:'2',panels:[{id:'a',preset_id:'p'},{id:'b',preset_id:'p'}]};
const body={data:{dashboard_revision:'2',observation_id:'obs',evaluated_at:'time',watermark:{},preset_revisions:{p:'3'},panels:[{panel_id:'a',preset_id:'p',count:'4'},{panel_id:'b',preset_id:'p',count:'4'}]}};
assert.equal(dashboardResult(body,definition,{p:'3'}),true);
body.data.panels[1].count='5';
assert.equal(dashboardResult(body,definition,{p:'3'}),false);
body.data.panels[1].count='4';
assert.equal(dashboardResult(body,definition,{p:'4'}),false);
body.data.panels[1].panel_id='a';
assert.equal(dashboardResult(body,definition,{p:'3'}),false);
""")


def test_k6_scripts_parse_as_javascript():
    if not NODE:
        pytest.skip("Node is required for syntax checking")
    for script in sorted(TOOLKIT.rglob("*.js")):
        subprocess.run([NODE, "--check", str(script)], check=True, capture_output=True, timeout=20)
