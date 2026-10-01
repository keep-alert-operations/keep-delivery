[CmdletBinding()]
param([ValidateSet('Start', 'Stop', 'Status')][string]$Action = 'Status', [switch]$Restart)
$ErrorActionPreference = 'Stop'
if ($Restart -and $Action -ne 'Start') { throw '-Restart is only valid with -Action Start.' }
$repository = Split-Path -Parent $PSScriptRoot
$state = Join-Path $repository '.local/runtime'
$python = Join-Path $repository 'backend/.venv/Scripts/python.exe'
$compose = Join-Path $repository 'deploy/local/compose.yaml'
$recordFile = Join-Path $state 'workers.json'
$stackMarker = Join-Path $state 'compose-stack.json'
$env:APPCHI_LOCAL_KAFKA_PORT = '19093'

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
Push-Location -LiteralPath $repository
try {
    New-Item -ItemType Directory -Path $state -Force | Out-Null
    $restartManaged = $Action -eq 'Start' -and ([bool]$Restart -or -not (Test-Path -LiteralPath $stackMarker))
    $records = if (Test-Path -LiteralPath $recordFile) { @(Get-Content -LiteralPath $recordFile -Raw | ConvertFrom-Json) } else { @() }
    if ($Action -eq 'Start' -and @($records | Where-Object { $_.Role -in @('assignment-cleanup','inactivity','maintenance','operators','live') -and (Owned $_) }).Count) { $restartManaged = $true }
    if ($Action -eq 'Stop' -or ($Action -eq 'Start' -and $restartManaged)) {
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
        if (Test-Path -LiteralPath (Join-Path $repository '.local/operators/credentials.json')) {
            Checked { & scripts/local-v2.ps1 -Action Stop }
        }
        Checked { & scripts/local-identity.ps1 -Action Stop }
        Checked { docker compose -p appchi-local-stack -f $compose stop }
        Write-Host 'Appchi processes and all local Compose services stopped; credentials and volumes preserved.'
        return
    }
    if ($Action -eq 'Status') {
        if (Test-Path -LiteralPath (Join-Path $repository '.local/operators/credentials.json')) {
            Checked { & scripts/local-v2.ps1 -Action Status }
        }
        Checked { & scripts/local-identity.ps1 -Action Status }
        foreach ($record in $records) {
            $healthPath = Join-Path $state "$($record.Role).health.json"
            $ready = if (Test-Path -LiteralPath $healthPath) { (Get-Content -LiteralPath $healthPath -Raw | ConvertFrom-Json).ready } else { $false }
            Write-Host "$($record.Role): running=$([bool](Owned $record)) last_turn_ready=$ready"
        }
        return
    }
    Checked { & scripts/local-identity.ps1 -Action Start -Restart:$restartManaged }
    $values = Get-Content -LiteralPath '.local/keycloak/app-environment.json' -Raw | ConvertFrom-Json
    foreach ($item in $values.PSObject.Properties) { [Environment]::SetEnvironmentVariable($item.Name,[string]$item.Value,'Process') }
    $env:APPCHI_DATABASE_URL = $env:APPCHI_LOCAL_WORKER_DATABASE_URL
    $env:APPCHI_POOL_SIZE = '1'
    Checked { & $python scripts/local_runtime.py require-kafka-cutover }
    Checked { & $python scripts/local_runtime.py migrate-search }
    Checked { & $python scripts/local_runtime.py probe }
    Checked { & $python -m appchi.search.local_bootstrap --evidence qualification/pytest-local-runtime-es.xml }
    if (Test-Path -LiteralPath '.local/runtime/correlation-preview-profile.json') {
        Checked { & $python scripts/local_correlation.py activate }
    }
    $roles = @('source','indexer')
    if (Test-Path -LiteralPath '.local/runtime/correlation-preview-profile.json') { $roles += 'correlation' }
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
    if (Test-Path -LiteralPath (Join-Path $repository '.local/operators/verification.json')) {
        Checked { & scripts/local-v2.ps1 -Action Start -Restart:$restartManaged }
    }
    @{ compose_project='appchi-local-stack'; initialized_at=[datetime]::UtcNow.ToString('o'); postgres_port=55433; elasticsearch_port=59201 } | ConvertTo-Json | Set-Content -LiteralPath $stackMarker
    Write-Host 'Core local Appchi is running: https://localhost:3443'
    if (Test-Path -LiteralPath '.local/runtime/correlation-preview-profile.json') {
        Write-Host 'Local correlation preview is configured; production qualification remains pending.'
    } else {
        Write-Host 'Maintenance, automatic correlation and protected recovery remain gated; optional local v2 uses scripts/local-v2.ps1.'
    }
} finally { Pop-Location }
