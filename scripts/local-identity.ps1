[CmdletBinding()]
param([ValidateSet('Start', 'Stop', 'Status')][string]$Action = 'Status', [switch]$Restart)
$ErrorActionPreference = 'Stop'
if ($Restart -and $Action -ne 'Start') { throw '-Restart is only valid with -Action Start.' }
$repository = Split-Path -Parent $PSScriptRoot
$state = Join-Path $repository '.local/keycloak'
$python = Join-Path $repository 'backend/.venv/Scripts/python.exe'
$compose = Join-Path $repository 'deploy/local/keycloak.compose.yaml'
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
        Invoke-Checked { docker compose -f $compose stop }
        Write-Host 'Local identity processes stopped; database, credentials and container volume preserved.'
        return
    }
    if ($Action -eq 'Status') {
        Invoke-Checked { docker compose -f $compose ps }
        foreach ($record in $records) { Write-Host "$($record.Role): $([bool](Get-OwnedProcess $record))" }
        return
    }
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
    Invoke-Checked { docker compose -f $compose up -d }
    Invoke-Checked { & $python scripts/local_identity.py database }
    Invoke-Checked { & $python scripts/local_identity.py environment }
    $values = Get-Content -LiteralPath (Join-Path $state 'app-environment.json') -Raw | ConvertFrom-Json
    foreach ($item in $values.PSObject.Properties) { [Environment]::SetEnvironmentVariable($item.Name, [string]$item.Value, 'Process') }
    Invoke-Checked { & $python scripts/local_identity.py wait }
    Invoke-Checked { & $python scripts/local_identity.py configure }
    $apiDatabase = $env:APPCHI_DATABASE_URL
    $env:APPCHI_DATABASE_URL = $env:APPCHI_LOCAL_WORKER_DATABASE_URL
    Invoke-Checked { & $python -m appchi.identity.local_keycloak_worker --config (Join-Path $state 'directory.json') --once --bootstrap }
    $node = (Get-Command node).Source
    $definitions = @(
        @{ Role='Api'; File=$python; Directory=$repository; Arguments='-m uvicorn appchi.main:create_app --factory --host 127.0.0.1 --port 58000 --no-access-log' },
        @{ Role='Directory'; File=$python; Directory=$repository; Arguments=('-m appchi.identity.local_keycloak_worker --config "' + (Join-Path $state 'directory.json') + '" --bootstrap') },
        @{ Role='Frontend'; File=$node; Directory=(Join-Path $repository 'frontend'); Arguments=('node_modules/next/dist/bin/next dev --hostname 127.0.0.1 --port 3443 --experimental-https --experimental-https-cert "' + (Join-Path $state 'tls/localhost.pem') + '" --experimental-https-key "' + (Join-Path $state 'tls/localhost-key.pem') + '"') }
    )
    $current = @()
    foreach ($definition in $definitions) {
        $existing = @($records | Where-Object { $_.Role -eq $definition.Role -and (Get-OwnedProcess $_) })
        if ($existing.Count) { $current += $existing; continue }
        $env:APPCHI_DATABASE_URL = if ($definition.Role -eq 'Directory') { $env:APPCHI_LOCAL_WORKER_DATABASE_URL } else { $apiDatabase }
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
