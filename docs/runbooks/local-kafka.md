# Local Kafka compatibility evidence

The local application transport check used the official Apache Kafka 4.0.0 Scala 2.13 archive, its SHA512 checksum, and the OpenJDK 24 runtime bundled with the isolated Elasticsearch distribution. This is a development fixture, not a company-installed version or infrastructure recommendation. See the [Apache Kafka quickstart](https://kafka.apache.org/40/getting-started/quickstart/) for the downloaded-distribution and KRaft setup model.

Archive: `https://archive.apache.org/dist/kafka/4.0.0/kafka_2.13-4.0.0.tgz`. Verified SHA512:

```text
00722AB0A6B954E0006994B8D589DCD8F26E1827C47F70B6E820FB45AA35945C19163B0F188CAF0CAF976C11F7AB005FD368C54E5851E899D2DE687A804A5EB9
```

All files were under workspace `.local/`. The combined KRaft broker/controller bound only `127.0.0.1:59092` and `127.0.0.1:59093`, with a unique data directory and development-only plaintext networking. Heap was 256–384 MiB. The hidden launcher ownership/start-time record is `.local/kafka-test-process.json`; startup and failure logs are `.local/kafka-test.out.log` and `.local/kafka-test.err.log`. No company broker was contacted.

## Executed result

`backend/tests/integration/test_kafka_pipeline.py` passed once against actual PostgreSQL and Kafka in 14.31 seconds. It verified durable outbox publication, immutable source references after processing, a simulated crash after PG receipt commit but before offset commit, idempotent replay, broker offset advancement only after PG proof, and an invalid record that leaves the committed offset behind the failed record. The test uses an isolated database, unique topic and unique consumer group, and requests topic deletion during cleanup.

**Environment compatibility failure:** after acknowledging the test-topic deletion, the native Windows broker raised `java.nio.file.AccessDeniedException` while renaming its topic log directory and shut down because the log directory failed. The application assertions passed before this asynchronous cleanup failure. This does not qualify the native broker's storage lifecycle, recovery, production operation or sustained load. The broker is no longer running; no success is claimed for topic deletion/recovery.

Do not point normal CI at this failed local instance. Run the test against disposable Kafka in the supplied supported nonproduction environment:

```powershell
$env:APPCHI_IDENTITY_TEST_ADMIN_URL = '<isolated PostgreSQL admin DSN>'
$env:APPCHI_TEST_KAFKA_BOOTSTRAP = '<disposable Kafka bootstrap addresses>'
backend/.venv/Scripts/python.exe -m pytest backend/tests/integration/test_kafka_pipeline.py -q --basetemp=.local/pytest-kafka
```

Review test privileges first: it creates/deletes a unique topic and creates/drops a unique PostgreSQL database. Unset the Kafka opt-in when no suitable broker is available; a skip is not a compatibility pass. Keep broker logs and actual service version alongside pytest evidence. Broker retention, storage, backup, HA and production topic allocation remain the operating team's responsibility.

## Local artifact handling

Retain the failed fixture and logs for diagnosis until no longer needed. Do not reuse its formatted directory for a fresh cluster. Before stopping any process, verify the recorded PID, start time and launcher path still match; never stop an unrelated Java process. Before removing local fixture files, resolve the exact target and verify it is a child of this workspace's `.local` directory. Native Windows cleanup must use literal PowerShell paths, without constructing deletion commands for another shell.
