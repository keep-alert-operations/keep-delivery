[CmdletBinding()]
param([ValidateSet('Start', 'Stop', 'Status', 'Refresh')][string]$Action = 'Status', [switch]$Restart)
$ErrorActionPreference = 'Stop'
if ($Restart -and $Action -ne 'Start') { throw '-Restart is only valid with -Action Start.' }
$repository = Split-Path -Parent $PSScriptRoot
$state = Join-Path $repository '.local/keycloak'
$python = Join-Path $repository '.venv/Scripts/python.exe'
$compose = Join-Path $repository 'deploy/local/compose.yaml'
$processFile = Join-Path $state 'processes.json'

function Invoke-Checked([scriptblock]$Command) {
    & $Command
    if ($LASTEXITCODE -ne 0) { throw "Local identity command failed (exit $LASTEXITCODE)." }
}

function Get-OwnedProcess($record) {
    $process = Get-Process -Id $record.Id -ErrorAction SilentlyContinue
    if ($process -and $process.StartTime.ToUniversalTime().Ticks -eq ([datetime]$record.StartTime).ToUniversalTime().Ticks) { return $process }
    return $null
}

Push-Location -LiteralPath $repository
try {
    $portJson = & $python scripts/local_v2.py ports
    if ($LASTEXITCODE -ne 0) { throw 'Invalid local port configuration; no services changed.' }
    $portValues = $portJson | ConvertFrom-Json
    foreach ($item in $portValues.PSObject.Properties) { [Environment]::SetEnvironmentVariable($item.Name, [string]$item.Value, 'Process') }
    $records = if (Test-Path -LiteralPath $processFile) { @(Get-Content -LiteralPath $processFile -Raw | ConvertFrom-Json) } else { @() }
    if ($Action -eq 'Stop') {
        foreach ($record in $records) {
            $owned = Get-OwnedProcess $record
            if ($owned) {
                # Next's server child belongs to the exact process started by this script.
                $children = @(Get-CimInstance Win32_Process -Filter "ParentProcessId=$($owned.Id)")
                foreach ($child in $children) { Stop-Process -Id $child.ProcessId -ErrorAction SilentlyContinue }
                Stop-Process -Id $owned.Id -ErrorAction SilentlyContinue
            }
        }
        Invoke-Checked { docker compose -p appchi-local-stack -f $compose stop keycloak }
        Write-Host 'Local identity processes stopped; credentials and container volumes preserved.'
        return
    }
    if ($Action -eq 'Status') {
        Invoke-Checked { docker compose -p appchi-local-stack -f $compose ps }
        foreach ($record in $records) { Write-Host "$($record.Role): $([bool](Get-OwnedProcess $record))" }
        return
    }
    if ($Action -eq 'Refresh') {
        $values = Get-Content -LiteralPath (Join-Path $state 'app-environment.json') -Raw | ConvertFrom-Json
        foreach ($item in $values.PSObject.Properties) { [Environment]::SetEnvironmentVariable($item.Name, [string]$item.Value, 'Process') }
        $env:APPCHI_DATABASE_URL = $env:APPCHI_LOCAL_WORKER_DATABASE_URL
        Invoke-Checked { & $python -m appchi.identity.local_keycloak_worker --config (Join-Path $state 'directory.json') --once }
        Write-Host 'Trusted local identity data refreshed once; no directory worker started.'
        return
    }
    # Retire only the directory process recorded and owned by this launcher.
    foreach ($record in @($records | Where-Object { $_.Role -eq 'Directory' })) {
        $owned = Get-OwnedProcess $record
        if ($owned) {
            foreach ($child in @(Get-CimInstance Win32_Process -Filter "ParentProcessId=$($owned.Id)")) { Stop-Process -Id $child.ProcessId -ErrorAction SilentlyContinue }
            Stop-Process -Id $owned.Id -ErrorAction SilentlyContinue
        }
    }
    $records = @($records | Where-Object { $_.Role -ne 'Directory' })
    if (Test-Path -LiteralPath $processFile) { $records | ConvertTo-Json -AsArray | Set-Content -LiteralPath $processFile -Encoding utf8 }
    if ($Restart) {
        foreach ($record in $records) {
            $owned = Get-OwnedProcess $record
            if ($owned) {
                foreach ($child in @(Get-CimInstance Win32_Process -Filter "ParentProcessId=$($owned.Id)")) { Stop-Process -Id $child.ProcessId -ErrorAction SilentlyContinue }
                Stop-Process -Id $owned.Id -ErrorAction SilentlyContinue
            }
        }
        $records = @()
    }
    Invoke-Checked { & $python scripts/local_identity.py prepare }
    Invoke-Checked { & $python scripts/local_v2.py prepare }
    Invoke-Checked { docker compose -p appchi-local-runtime -f $compose stop kafka redis }
    Invoke-Checked { docker compose -p appchi-local-identity -f deploy/local/keycloak.compose.yaml stop keycloak }
    Invoke-Checked { docker compose -p appchi-local-operators -f deploy/local/operators.compose.yaml stop }
    Invoke-Checked { docker compose -p appchi-local-stack -f $compose up -d }
    Invoke-Checked { & $python scripts/local_identity.py database }
    Invoke-Checked { & $python scripts/local_identity.py environment }
    $values = Get-Content -LiteralPath (Join-Path $state 'app-environment.json') -Raw | ConvertFrom-Json
    foreach ($item in $values.PSObject.Properties) { [Environment]::SetEnvironmentVariable($item.Name, [string]$item.Value, 'Process') }
    Invoke-Checked { & $python scripts/local_identity.py wait }
    Invoke-Checked { & $python scripts/local_identity.py configure }
    $apiDatabase = $env:APPCHI_DATABASE_URL
    $env:APPCHI_DATABASE_URL = $env:APPCHI_LOCAL_WORKER_DATABASE_URL
    Invoke-Checked { & $python -m appchi.identity.local_keycloak_worker --config (Join-Path $state 'directory.json') --once --bootstrap }
    $env:APPCHI_DATABASE_URL = $apiDatabase
    $node = (Get-Command node).Source
    $definitions = @(
        @{ Role='Api'; File=$python; Directory=$repository; Arguments='-m uvicorn appchi.main:create_app --factory --host 127.0.0.1 --port 58000 --no-access-log' },
        @{ Role='Frontend'; File=$node; Directory=(Join-Path (Split-Path -Parent $repository) 'appchi-frontend'); Arguments=('node_modules/next/dist/bin/next dev --hostname 127.0.0.1 --port 3443 --experimental-https --experimental-https-cert "' + (Join-Path $state 'tls/localhost.pem') + '" --experimental-https-key "' + (Join-Path $state 'tls/localhost-key.pem') + '"') }
    )
    $current = @()
    foreach ($definition in $definitions) {
        $existing = @($records | Where-Object { $_.Role -eq $definition.Role -and (Get-OwnedProcess $_) })
        if ($existing.Count) { $current += $existing; continue }
        $process = Start-Process -FilePath $definition.File -ArgumentList $definition.Arguments -WorkingDirectory $definition.Directory -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $state "$($definition.Role).out.log") -RedirectStandardError (Join-Path $state "$($definition.Role).err.log")
        $current += @{ Role=$definition.Role; Id=$process.Id; StartTime=$process.StartTime.ToUniversalTime().ToString('o') }
        $current | ConvertTo-Json -AsArray | Set-Content -LiteralPath $processFile -Encoding utf8
    }
    $probe = @'
import ssl,time,httpx
from pathlib import Path
certificate=Path('.local/keycloak/tls/localhost.pem')
with httpx.Client(verify=ssl.create_default_context(cafile=str(certificate)),trust_env=False) as client:
    for attempt in range(60):
        try:
            api=client.get('http://127.0.0.1:58000/livez',timeout=2)
            ui=client.get('https://localhost:3443/auth/session',timeout=3)
            if api.status_code==200 and ui.status_code==401:
                print('API and HTTPS frontend are accepting requests.')
                break
        except httpx.HTTPError:
            pass
        time.sleep(1)
    else:
        raise SystemExit('API/frontend startup failed; inspect .local/keycloak logs.')
'@
    Invoke-Checked { & $python -c $probe }
    foreach ($record in $current) { if (-not (Get-OwnedProcess $record)) { throw "$($record.Role) exited during startup; inspect its local log." } }
    Write-Host 'Appchi: https://localhost:3443 | Keycloak: https://localhost:58443/admin/'
    Write-Host 'Local username/password credentials: .local/keycloak/credentials.json'
} finally { Pop-Location }
