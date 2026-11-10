# Local startup repair — 2026-10-04

Scope: the isolated Windows development stack. The user requested fixing startup
problems and running the system. The core launcher completed successfully,
including the optional local v2 gateway. No company service or production
deployment was changed.

## Acceptance and repair

Required: preserve existing data/credentials, complete the documented Kafka
cutover prerequisites, start the Product API/public ingestion/UI/source/indexer,
and verify real sign-in, v1/v2 application/search and local tenant isolation.

1. Inspected repository state, owned processes, ports, Docker mounts and volumes.
   Stopped legacy containers pointed to the original checkout's ignored `.local`
   directory. Its matching PostgreSQL, identity/encryption, Kong/Grafana and TLS
   credentials remained available. Recovered those files into delivery, backed
   up the earlier newly generated values, and regenerated environment paths.
   Did not copy stale process records or activate old preview evidence. Existing
   PostgreSQL/Elasticsearch/Keycloak/Kong/Grafana/Kafka volumes were preserved.
2. Removed the database-environment prerequisite from pure v2 file preparation.
   The launcher needs those files before database/environment bootstrap exists.
   Repeated preparation still preserves existing credentials.
3. Applied the dedicated Alembic migration runner against the existing local
   schema, adopting its unchanged 44-file SQL history. Verified all 28 legacy
   source admissions were applied, all 60 legacy projections visible, and no
   pending maintenance or correlation effects remained before cutover.
4. Verified exact authoritative references for the five fenced Global changed
   events (orders 39–43, lease generation 35), and all records present in the
   old-protocol broker topics. Created
   `.local/runtime/pre-startup-repair-20261004.dump` before the narrowly guarded
   repair. Recorded `legacy-pipeline-fence-revalidated` in local audit, cleared
   only that stale lease, and published the five events through the existing
   relay. Historical quarantine records remain preserved; no fake receipt or
   publish acknowledgement was inserted.
5. Confirmed legacy consumers were stopped and fenced local admission. Ran the
   documented offline cutover at `2026-10-04T07:08:00Z`: new source group starts
   at received offset 4; correlation/indexer groups start at changed offset 17.
   Both topics have one partition. The database Kafka protocol control is enabled.
6. Added the separate public Ingestion API on port 58001 to the identity launcher
   and startup probe. Product API port 58000 has no admission routes. Updated
   v2 verification and the real browser check to use the current Kafka admission
   response and asynchronous tenant search, without obsolete receipt lookup or
   client-key deduplication claims.
7. Requalified the relocated binding for the synthetic Local Demo Sender against
   the same local CA, endpoints, versions, Grafana organization and current Kong
   node; recorded `local-operator-binding-relocated`. The pending operation had
   no executor ledger effects before this rebind. Required normal provisioning
   and serving-node proof remained in place.
8. Normal demo key rotation exposed an executor bug: the candidate secret was
   also sent when probing the old credential ID. Corrected proof selection to
   send it only with its candidate/current ID. Regression checks cover prepared
   and activated rotation policies. Retried the same durable operation through
   the normal Product API; it completed. The old current key became retiring
   under the normal fixed 24-hour overlap; no second candidate was created.

## Source combination

The prepared delivery virtual environment uses coordinated editable checkouts;
release dependency pins were not changed. These HEAD revisions plus the noted
working changes were exercised. This is not a new published release manifest.

| Repository | HEAD | Working changes exercised |
|---|---|---|
| keep-api | `0b8a1a3` | Executor proof fix and rotation regression checks |
| keep-frontend | `468ba80` | Real local runtime browser check |
| keep-delivery | `2d1fc70` | Launcher, v2 preparation/verification, regression checks, fresh ES report, runbooks/evidence |
| keep-ingestion | `0d2ee23` | None |
| keep-processor | `4f7a3fc` | None |
| keep-correlation | `ad68f9f` | None; worker not activated |
| keep-indexer | `ff88bec` | None |
| keep-domain | `830d4f6` | None |
| keep-contracts | `42107e3` | None |
| keep-migrations | `1ebe4cf` | None; existing local chain migrated |
| keep-design | `7bf631c` | None |

Delivery's pre-existing workspace setup/docs/instruction edits and untracked
instruction test were preserved. No repository commits, package publication or
release pin advancement were performed. Publishing the API fix for noneditable
consumers would require coordinated revision/pin updates and release checks.

## Executed checks

Commands below run from their owning repository; Python commands use the
prepared delivery environment. Temporary-directory/native-library/Docker/browser
access required host execution where the Windows sandbox denied it.

| Repository | Command/check | Actual outcome |
|---|---|---|
| delivery | `.venv/Scripts/python.exe -m pytest tests/unit/test_local_v2_setup.py tests/unit/test_local_identity_setup.py -q` | 19 passed; deprecation/cache warnings |
| API | `../keep-delivery/.venv/Scripts/python.exe -m pytest tests/unit/test_operator_executor.py tests/unit/test_operator_keys.py tests/unit/test_operator_remote.py -q` | 37 passed |
| API | Ruff on executor and its unit file | Passed |
| delivery | Ruff on `local_v2.py`, `local_v2_verify.py`, `test_local_v2_setup.py` | Passed |
| frontend | `node node_modules/typescript/bin/tsc --noEmit` | Passed |
| frontend | Prettier check on `tests/local-runtime.spec.ts` | Passed after formatting |
| frontend | `KEEP_LOCAL_RUNTIME_TEST=1`, `node node_modules/@playwright/test/cli.js test --config playwright.local-runtime.config.ts` | 1 passed, 24.8 seconds |
| delivery | Real Elasticsearch search/count compatibility checks below | 2 passed; fresh [JUnit report](pytest-local-runtime-es.xml) |
| delivery | `scripts/local-runtime.ps1 -Action Start` | Exit 0; both required workers completed healthy turns; local v2 verified |
| delivery | `scripts/local-runtime.ps1 -Action Status` | All eight dependency services healthy; API/public ingestion/UI/private ingestion/proof/source/indexer running |
| delivery | `.venv/Scripts/python.exe scripts/local_v2_verify.py verify` via launcher | 17 live gateway/authorization checks passed |
| delivery | `.venv/Scripts/python.exe scripts/local_v2_verify.py send` | 3 Kafka-admitted alerts applied/searchable in Local Demo; Global details returned 404 |
| delivery | PowerShell parser on `scripts/local-identity.ps1` | No syntax errors |
| delivery | `.venv/Scripts/python.exe qualification/verify_documents.py` | 107 documents, 566 source entries; no errors; REV-01..REV-38 preserved |

The real ES command used disposable test indices, with explicit owning-test
paths and report classnames accepted by the existing search bootstrap:

```powershell
$env:KEEP_TEST_ELASTICSEARCH_URL='http://127.0.0.1:59201'
$env:PYTHONPATH=(Resolve-Path '../keep-indexer/tests/compatibility').Path
.venv/Scripts/python.exe -m pytest -c pyproject.toml --rootdir=.. ../keep-indexer/tests/compatibility/test_search_elasticsearch.py ../keep-domain/tests/compatibility/test_count_elasticsearch.py -q --junitxml=qualification/pytest-local-runtime-es.xml
```

The cutover used the recovered worker environment while admission and legacy
workers were stopped:

```powershell
.venv/Scripts/python.exe -m keep.kafka_cutover --ingress-and-legacy-workers-stopped --local-config .local/runtime/kafka-protocol.json
```

The API regression failed before the proof fix and passed afterward. Earlier
browser runs found outdated note locators; the final check scopes to rendered
saved notes and reloads the page to confirm persistence. An intermediate delivery
unit run had three sandbox temporary-directory errors; host rerun passed all 19.
An initial ES report lacked the required test classnames; the corrected command
above passed without relaxing evidence validation.

Browser checks exercised real password sign-in, native v1, source application,
ES feed, connected live hints, assignment/acknowledgement, a persisted note and
updated search. Feed/detail screenshots were inspected. Nonsecret local result
metadata/screenshots remain in ignored `.local/runtime/browser-evidence.json`,
`alerts.png`, `alert-detail.png`; v2 metadata is under ignored
`.local/operators/verification.json` and `alerts-evidence.json`. Synthetic alerts
and notes intentionally remain in the development database.

## Running state and limitations

Open `https://localhost:3443`; sign in as `local-admin` using the existing password
under `users.local-admin.password` in ignored `.local/keycloak/credentials.json`.
The [runtime guide](../docs/local-runtime.md) lists service URLs and start/stop
commands from delivery. All exposed dependency ports remain bound to loopback.

Source and indexer are healthy. The existing correlation rule and historical
local-preview gate were preserved, but its refreshed profile/evidence are absent
and the correlation worker is not running. Maintenance, audit protection and
protected recovery remain gated. No company LDAP/Grafana/Kong integration,
multi-node propagation, sustained capacity, production migration/rollback or
release qualification was executed. DEL-01 through DEL-08 remain open. Deferred
sweep/directory/operator/live-worker scope is unchanged.
