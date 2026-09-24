[CmdletBinding()]
param([ValidateSet('Start', 'Stop', 'Status')][string]$Action = 'Status', [switch]$Restart)
$ErrorActionPreference = 'Stop'
if ($Restart -and $Action -ne 'Start') { throw '-Restart is only valid with -Action Start.' }
$repository = Split-Path -Parent $PSScriptRoot
$state = Join-Path $repository '.local/runtime'
$python = Join-Path $repository 'backend/.venv/Scripts/python.exe'
$compose = Join-Path $repository 'deploy/local/compose.yaml'
$recordFile = Join-Path $state 'workers.json'
$env:APPCHI_LOCAL_KAFKA_PORT = '19092'

function Checked([scriptblock]$Command) {
    & $Command
    if ($LASTEXITCODE -ne 0) { throw "Local runtime command failed (exit $LASTEXITCODE)." }
}
function Owned($record) {
    $process = Get-Process -Id $record.Id -ErrorAction SilentlyContinue
    if ($process -and $process.StartTime.ToUniversalTime().Ticks -eq ([datetime]$record.StartTime).ToUniversalTime().Ticks) { return $process }
    return $null
}
function Worker-Id($record) {
    # Windows venv python.exe is a launcher; its child writes the heartbeat.
    for ($attempt=0; $attempt -lt 20; $attempt++) {
        $child = Get-CimInstance Win32_Process -Filter "ParentProcessId=$($record.Id)" | Where-Object { $_.CommandLine -and $_.CommandLine.EndsWith("appchi.worker $($record.Role)") } | Select-Object -First 1
        if ($child) { return $child.ProcessId }
        Start-Sleep -Milliseconds 100
    }
    throw "Cannot identify the owned $($record.Role) worker child."
}
function Listening([int]$Port) {
    return [bool](Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue)
}
function Start-NativeStores {
    if (-not (Listening 55432)) {
        $pgControl = 'C:/Program Files/PostgreSQL/18/bin/pg_ctl.exe'
        $pgData = Join-Path $repository '.local/pgdata'
        if (-not (Test-Path -LiteralPath $pgControl) -or -not (Test-Path -LiteralPath (Join-Path $pgData 'PG_VERSION'))) {
            throw 'The existing local PostgreSQL 18 installation/data directory is required; see docs/local-runtime.md.'
        }
        $arguments = 'start -D "' + $pgData + '" -o "-h 127.0.0.1 -p 55432" -l "' + (Join-Path $state 'postgres.log') + '" -w -t 30'
        $process = Start-Process -FilePath $pgControl -ArgumentList $arguments -WindowStyle Hidden -Wait -PassThru
        if ($process.ExitCode -ne 0) { throw 'Existing local PostgreSQL could not start; inspect .local/runtime/postgres.log.' }
    }
    if (-not (Listening 59200)) {
        $runtime = Join-Path $repository '.local/elasticsearch-9.0.0'
        $config = Join-Path $runtime 'config/elasticsearch.yml'
        if (-not (Test-Path -LiteralPath $config)) { throw 'The existing local Elasticsearch runtime/configuration is required; see docs/runbooks/local-elasticsearch.md.' }
        $content = Get-Content -LiteralPath $config -Raw
        if ($content -notmatch '(?m)^network.host: 127\.0\.0\.1\s*$' -or $content -notmatch '(?m)^http.port: 59200\s*$') { throw 'Refusing to start Elasticsearch without the expected loopback binding.' }
        $launcher = Join-Path $state 'start-elasticsearch.ps1'
        Set-Content -LiteralPath $launcher -Value "& (Join-Path `$PSScriptRoot '../elasticsearch-9.0.0/bin/elasticsearch.bat')"
        $previousOptions = $env:ES_JAVA_OPTS
        try {
            $env:ES_JAVA_OPTS = '-Xms512m -Xmx512m'
            $process = Start-Process -FilePath (Get-Command pwsh).Source -ArgumentList ('-NoProfile -NonInteractive -File "' + $launcher + '"') -WorkingDirectory $runtime -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $state 'elasticsearch.out.log') -RedirectStandardError (Join-Path $state 'elasticsearch.err.log')
            @{ Id=$process.Id; StartTime=$process.StartTime.ToUniversalTime().ToString('o') } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $state 'elasticsearch-process.json')
        } finally { [Environment]::SetEnvironmentVariable('ES_JAVA_OPTS',$previousOptions,'Process') }
        for ($attempt=0; $attempt -lt 90 -and -not (Listening 59200); $attempt++) { Start-Sleep -Seconds 1 }
        if (-not (Listening 59200)) { throw 'Elasticsearch did not start; inspect .local/runtime/elasticsearch.err.log.' }
    }
}
Push-Location -LiteralPath $repository
try {
    New-Item -ItemType Directory -Path $state -Force | Out-Null
    $records = if (Test-Path -LiteralPath $recordFile) { @(Get-Content -LiteralPath $recordFile -Raw | ConvertFrom-Json) } else { @() }
    if ($Action -eq 'Stop' -or $Restart) {
        foreach ($record in $records) {
            $process = Owned $record
            if ($process) {
                foreach ($child in @(Get-CimInstance Win32_Process -Filter "ParentProcessId=$($process.Id)")) { Stop-Process -Id $child.ProcessId -ErrorAction SilentlyContinue }
                Stop-Process -Id $process.Id -ErrorAction SilentlyContinue
            }
        }
        $records = @()
    }
    if ($Action -eq 'Stop') {
        Checked { & scripts/local-identity.ps1 -Action Stop }
        Checked { docker compose -p appchi-local-runtime -f $compose stop kafka redis }
        Write-Host 'Appchi workers, UI/API, Keycloak, Kafka and Redis stopped. Existing native PostgreSQL/Elasticsearch and all data preserved.'
        return
    }
    if ($Action -eq 'Status') {
        Checked { & scripts/local-identity.ps1 -Action Status }
        Checked { docker compose -p appchi-local-runtime -f $compose ps kafka redis }
        foreach ($record in $records) {
            $healthPath = Join-Path $state "$($record.Role).health.json"
            $ready = if (Test-Path -LiteralPath $healthPath) { (Get-Content -LiteralPath $healthPath -Raw | ConvertFrom-Json).ready } else { $false }
            Write-Host "$($record.Role): running=$([bool](Owned $record)) last_turn_ready=$ready"
        }
        return
    }
    Start-NativeStores
    Checked { docker compose -p appchi-local-runtime -f $compose up -d kafka redis }
    Checked { & scripts/local-identity.ps1 -Action Start -Restart:$Restart }
    $values = Get-Content -LiteralPath '.local/keycloak/app-environment.json' -Raw | ConvertFrom-Json
    foreach ($item in $values.PSObject.Properties) { [Environment]::SetEnvironmentVariable($item.Name,[string]$item.Value,'Process') }
    $env:APPCHI_DATABASE_URL = $env:APPCHI_LOCAL_WORKER_DATABASE_URL
    $env:APPCHI_POOL_SIZE = '1'
    Checked { & $python scripts/local_runtime.py probe }
    Checked { & $python -m appchi.search.local_bootstrap --evidence qualification/pytest-local-runtime-es.xml }
    $roles = @('source','indexer','relay','source-hints','indexer-hints','correlation-hints','live','assignment-cleanup','inactivity')
    $current = @()
    foreach ($role in $roles) {
        $existing = @($records | Where-Object { $_.Role -eq $role -and (Owned $_) })
        if ($existing.Count) {
            $existing[0] | Add-Member -NotePropertyName WorkerId -NotePropertyValue (Worker-Id $existing[0]) -Force
            $current += $existing[0]
            continue
        }
        $env:APPCHI_WORKER_HEALTH_PATH = Join-Path $state "$role.health.json"
        $process = Start-Process -FilePath $python -ArgumentList "-m appchi.worker $role" -WorkingDirectory $repository -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $state "$role.out.log") -RedirectStandardError (Join-Path $state "$role.err.log")
        $record = [pscustomobject]@{ Role=$role; Id=$process.Id; StartTime=$process.StartTime.ToUniversalTime().ToString('o'); WorkerId=0 }
        $record.WorkerId = Worker-Id $record
        $current += $record
        $current | ConvertTo-Json -AsArray | Set-Content -LiteralPath $recordFile -Encoding utf8
    }
    $current | ConvertTo-Json -AsArray | Set-Content -LiteralPath $recordFile -Encoding utf8
    Checked { & $python scripts/local_runtime.py wait-workers }
    Write-Host 'Core local Appchi is running: https://localhost:3443'
    Write-Host 'Maintenance, automatic correlation, v2/Kong/Grafana and protected recovery remain gated; see docs/local-runtime.md.'
} finally { Pop-Location }
