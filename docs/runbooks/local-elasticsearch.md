# Legacy native Elasticsearch compatibility source

The normal Appchi local runtime uses the Elasticsearch 9.0.0 container from the
unified [local Compose stack](../local-runtime.md) on port 59201. This runbook
describes the retained Windows ZIP instance on ports 59200/59300, used as the
local migration source/rollback copy. It is not needed to start Appchi. Its
authentication is disabled; keep it bound to loopback and do not point it at
company services.

Elastic's Windows distribution includes OpenJDK, so a separate Java installation
is unnecessary. Use the [official Windows installation documentation](https://www.elastic.co/docs/deploy-manage/deploy/self-managed/install-elasticsearch-with-zip-on-windows).
The pinned version here matches this repository's development Compose selection;
it is not a company-supported version claim.

## Download and verify

Run PowerShell 7 from the repository root. Allow approximately 489 MB for the ZIP
plus extracted runtime/data space. Download only the official artifact and verify
its published checksum before executing it:

```powershell
$ErrorActionPreference = 'Stop'
$local = Join-Path (Get-Location) '.local'
[void](New-Item -ItemType Directory -Path $local -Force)
$archive = Join-Path $local 'elasticsearch-9.0.0-windows-x86_64.zip'
$source = 'https://artifacts.elastic.co/downloads/elasticsearch/elasticsearch-9.0.0-windows-x86_64.zip'
curl.exe --fail --location --connect-timeout 10 --max-time 180 --output $archive $source
if ($LASTEXITCODE) { throw 'Elasticsearch download failed' }
curl.exe --fail --location --connect-timeout 10 --max-time 30 --output ($archive + '.sha512') ($source + '.sha512')
if ($LASTEXITCODE) { throw 'Checksum download failed' }
$expected = ((Get-Content -LiteralPath ($archive + '.sha512') -Raw) -split '\s+')[0]
$observed = (Get-FileHash -LiteralPath $archive -Algorithm SHA512).Hash.ToLowerInvariant()
if ($observed -ne $expected) { throw 'Official SHA-512 mismatch' }
if ($observed -ne '3a23ff239c4a48590feeecc2886aead62ada9767cd2a349e9e59cc3133f1ae17f9f4d9c75348bee7a73037b1ac5906f5ff76a1f3ac403c9854dde445e6646e2f') {
    throw 'Archive differs from the verified development artifact'
}
$runtime = Join-Path $local 'elasticsearch-9.0.0'
if (-not (Test-Path -LiteralPath $runtime)) {
    tar.exe -xf $archive -C $local
    if ($LASTEXITCODE) { throw 'Extraction failed' }
}
```

The [official archive](https://artifacts.elastic.co/downloads/elasticsearch/elasticsearch-9.0.0-windows-x86_64.zip)
and its [SHA-512 file](https://artifacts.elastic.co/downloads/elasticsearch/elasticsearch-9.0.0-windows-x86_64.zip.sha512)
were fetched and matched during the recorded run. A partial download must not be
extracted or executed. Do not weaken certificate validation to work around network
restrictions.

## Configure and start one owned process

Check that neither test port is already listening. If another process owns either
port, investigate it; do not terminate an unrelated process. Ensure any earlier
instance from this runbook has been stopped before replacing its PID record.

```powershell
netstat.exe -ano -p tcp | Select-String -Pattern ':(59200|59300)\s+.*LISTENING'
```

With both ports free, continue in the same terminal. The data directory is unique
for each run; the archive/runtime remain under the ignored `.local` directory.

```powershell
$instance = Join-Path $local ('esdata-' + [Guid]::NewGuid().ToString('N'))
[void](New-Item -ItemType Directory -Path $instance)
$data = (Join-Path $instance 'data').Replace('\', '/')
$logs = (Join-Path $instance 'logs').Replace('\', '/')
@"
cluster.name: appchi-disposable-compatibility
node.name: appchi-local-test
network.host: 127.0.0.1
http.port: 59200
transport.port: 59300
discovery.type: single-node
xpack.security.enabled: false
xpack.security.autoconfiguration.enabled: false
xpack.ml.enabled: false
ingest.geoip.downloader.enabled: false
path.data: '$data'
path.logs: '$logs'
"@ | Set-Content -LiteralPath (Join-Path $runtime 'config/elasticsearch.yml')
$launcher = Join-Path $local 'start-es-compatibility.ps1'
Set-Content -LiteralPath $launcher -Value '& (Join-Path $PSScriptRoot ''elasticsearch-9.0.0/bin/elasticsearch.bat'')'
$oldJavaOptions = $env:ES_JAVA_OPTS
try {
    $env:ES_JAVA_OPTS = '-Xms512m -Xmx512m'
    $process = Start-Process -FilePath (Get-Command pwsh).Source -WindowStyle Hidden -PassThru `
        -ArgumentList @('-NoProfile', '-NonInteractive', '-File', ('"' + $launcher + '"')) `
        -WorkingDirectory $runtime `
        -RedirectStandardOutput (Join-Path $instance 'stdout.log') `
        -RedirectStandardError (Join-Path $instance 'stderr.log')
    @{
        pid = $process.Id
        started_utc = $process.StartTime.ToUniversalTime().ToString('o')
        launcher = $launcher
        instance = $instance
        port = 59200
    } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $local 'es-compatibility-process.json')
} finally {
    [Environment]::SetEnvironmentVariable('ES_JAVA_OPTS', $oldJavaOptions, 'Process')
}
```

This starts a hidden foreground launcher/process tree, not a registered Windows
service. The supplied 512 MiB heap is a development test limit, not measured
production sizing. Startup can take several minutes on a cold Windows runtime.
Inspect the recorded instance's `stdout.log` and `stderr.log`, then verify:

```powershell
Invoke-RestMethod -Uri 'http://127.0.0.1:59200/' -TimeoutSec 5
netstat.exe -ano -p tcp | Select-String -Pattern ':(59200|59300)\s+.*LISTENING'
```

The endpoint must report `9.0.0`, and both listeners must show `127.0.0.1`. During
the recorded run, sandboxed Java failed while resolving the bundled
`jdk/conf/security/java.security` path. An explicitly approved launch outside that
sandbox succeeded; no Java security file, certificate check or application access
control was disabled to address that failure.

## Run the actual compatibility suite

```powershell
$previousPythonPath = $env:PYTHONPATH
$previousSearch = $env:APPCHI_TEST_ELASTICSEARCH_URL
try {
    $env:PYTHONPATH = $null
    $env:APPCHI_TEST_ELASTICSEARCH_URL = 'http://127.0.0.1:59200'
    ./.venv/Scripts/python.exe -m pytest ../appchi-indexer/tests/compatibility ../appchi-domain/tests/compatibility -q
    if ($LASTEXITCODE) { throw 'Real Elasticsearch compatibility failed' }
} finally {
    [Environment]::SetEnvironmentVariable('PYTHONPATH', $previousPythonPath, 'Process')
    [Environment]::SetEnvironmentVariable('APPCHI_TEST_ELASTICSEARCH_URL', $previousSearch, 'Process')
}
```

The suites come from the indexer and domain checkouts beside delivery; keep the
indexer suite first, because the domain count test reuses its fixture. Each fixture
creates a unique `appchi-test-*` index and removes its own index on normal
teardown. Tests use the real mapping, CEL translation/search, PIT traversal
and projection/tombstone paths. No route mock or in-memory substitute supplies the
Elasticsearch result.

## Stop only this run's process tree

Use the recorded launcher PID and creation time together to avoid targeting a
reused PID. The example keeps the ZIP, runtime, logs and disposable data for
inspection; it performs no recursive deletion.

```powershell
$record = Get-Content -LiteralPath '.local/es-compatibility-process.json' -Raw | ConvertFrom-Json
$ownedProcess = Get-Process -Id $record.pid -ErrorAction SilentlyContinue
if ($ownedProcess) {
    if ($ownedProcess.StartTime.ToUniversalTime().ToString('o') -ne $record.started_utc) {
        throw 'PID was reused; do not terminate it'
    }
    taskkill.exe /PID $record.pid /T /F
    if ($LASTEXITCODE) { throw 'Owned process shutdown failed; inspect the recorded process tree' }
}
netstat.exe -ano -p tcp | Select-String -Pattern ':(59200|59300)\s+.*LISTENING'
```

If permission restrictions prevent inspecting or stopping the recorded tree, use
the same explicitly approved context that launched it. Do not use a broad
`java.exe`/`pwsh.exe` kill command.

## Recorded evidence and limits

On 2026-09-22, the official Windows archive's SHA-512 matched, Elasticsearch 9.0.0
started with bundled OpenJDK 24 and Lucene 10.1.0, and `netstat` confirmed both
listeners on loopback. `backend/tests/compatibility` completed with **2 passed in
10.43 seconds**. The local evidence and launcher identity were recorded in
`.local/es-compatibility-evidence.json` and `.local/es-compatibility-process.json`.
The parent verification task retained the instance for its final combined checks
and owns its subsequent shutdown.

This establishes local development compatibility only. Company Elasticsearch
configuration/version, private-network TLS/authentication, OpenShift deployment,
capacity, recovery, availability and the complete Compose stack remain unqualified.
