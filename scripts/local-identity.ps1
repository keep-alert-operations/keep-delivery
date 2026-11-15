#Requires -Version 7
[CmdletBinding()]
param([ValidateSet('Start', 'Stop', 'Status', 'Refresh')][string]$Action = 'Status', [switch]$Restart)
$ErrorActionPreference = 'Stop'
if ($Restart -and $Action -ne 'Start') { throw '-Restart is only valid with -Action Start.' }
$repository = Split-Path -Parent $PSScriptRoot
$state = Join-Path $repository '.local/identity'
$python = Join-Path $repository '.venv/Scripts/python.exe'
$compose = Join-Path $repository 'deploy/local/compose.yaml'
$processFile = Join-Path $state 'processes.json'
# The mock ADAPI (REV-73) serves .local/identity/adapi-directory.json on loopback only.
$adapi = @{ Role='Adapi'; File=$python; Directory=$repository; Arguments=('scripts/local_adapi.py --host 127.0.0.1 --port 58092 --data "' + (Join-Path $state 'adapi-directory.json') + '" --client-id-env KEEP_ADAPI_CLIENT_ID') }

function Invoke-Checked([scriptblock]$Command) {
    & $Command
    if ($LASTEXITCODE -ne 0) { throw "Local identity command failed (exit $LASTEXITCODE)." }
}

function Get-OwnedProcess($record) {
    $process = Get-Process -Id $record.Id -ErrorAction SilentlyContinue
    if ($process -and $process.StartTime.ToUniversalTime().Ticks -eq ([datetime]$record.StartTime).ToUniversalTime().Ticks) { return $process }
    return $null
}

function Stop-Owned($record) {
    $owned = Get-OwnedProcess $record
    if ($owned) {
        # Next's server child and the venv launcher's Python child belong to the exact process started by this script.
        foreach ($child in @(Get-CimInstance Win32_Process -Filter "ParentProcessId=$($owned.Id)")) { Stop-Process -Id $child.ProcessId -ErrorAction SilentlyContinue }
        Stop-Process -Id $owned.Id -ErrorAction SilentlyContinue
    }
}

function Save-Records {
    @($script:records) | ConvertTo-Json -AsArray | Set-Content -LiteralPath $processFile -Encoding utf8
}

function Start-Owned([hashtable]$Definition) {
    if (@($script:records | Where-Object { $_.Role -eq $Definition.Role -and (Get-OwnedProcess $_) }).Count) { return }
    $process = Start-Process -FilePath $Definition.File -ArgumentList $Definition.Arguments -WorkingDirectory $Definition.Directory -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $state "$($Definition.Role).out.log") -RedirectStandardError (Join-Path $state "$($Definition.Role).err.log")
    $record = [pscustomobject]@{ Role=$Definition.Role; Id=$process.Id; StartTime=$process.StartTime.ToUniversalTime().ToString('o') }
    $script:records = @($script:records | Where-Object { $_.Role -ne $Definition.Role }) + @($record)
    Save-Records
}

function Import-Environment {
    $values = Get-Content -LiteralPath (Join-Path $state 'app-environment.json') -Raw | ConvertFrom-Json
    foreach ($item in $values.PSObject.Properties) { [Environment]::SetEnvironmentVariable($item.Name, [string]$item.Value, 'Process') }
}

function Invoke-DirectoryRefresh([switch]$Bootstrap) {
    # One ADAPI publication as the worker login; it starts no directory worker and exits
    # non-zero naming the roots that failed.
    $apiDatabase = $env:KEEP_DATABASE_URL
    $env:KEEP_DATABASE_URL = $env:KEEP_LOCAL_WORKER_DATABASE_URL
    $env:KEEP_DIRECTORY_BOOTSTRAP_QUALIFIED = if ($Bootstrap) { 'true' } else { 'false' }
    try { Invoke-Checked { & $python -m keep.worker directory --once } }
    finally {
        $env:KEEP_DATABASE_URL = $apiDatabase
        Remove-Item Env:KEEP_DIRECTORY_BOOTSTRAP_QUALIFIED -ErrorAction SilentlyContinue
    }
}

function Stop-LegacyIdentityContainers {
    # REV-73 removed Keycloak. Stop (never delete) containers an older setup left running:
    # the keycloak service of this project and the old keep-local-identity project.
    # Their volumes are kept.
    $ids = @(docker ps -q --filter 'label=com.docker.compose.project=keep-local-stack' --filter 'label=com.docker.compose.service=keycloak')
    if ($LASTEXITCODE -ne 0) { throw 'Cannot list local containers.' }
    $ids += @(docker ps -q --filter 'label=com.docker.compose.project=keep-local-identity')
    if ($LASTEXITCODE -ne 0) { throw 'Cannot list local containers.' }
    $ids = @($ids | Where-Object { $_ } | Select-Object -Unique)
    if ($ids.Count) {
        Invoke-Checked { docker stop @ids | Out-Null }
        Write-Host "Stopped $($ids.Count) legacy identity container(s); their volumes were kept."
    }
}

Push-Location -LiteralPath $repository
try {
    $portJson = & $python scripts/local_v2.py ports
    if ($LASTEXITCODE -ne 0) { throw 'Invalid local port configuration; no services changed.' }
    $portValues = $portJson | ConvertFrom-Json
    foreach ($item in $portValues.PSObject.Properties) { [Environment]::SetEnvironmentVariable($item.Name, [string]$item.Value, 'Process') }
    $script:records = if (Test-Path -LiteralPath $processFile) { @(Get-Content -LiteralPath $processFile -Raw | ConvertFrom-Json) } else { @() }
    if ($Action -eq 'Stop') {
        foreach ($record in $script:records) { Stop-Owned $record }
        Invoke-Checked { docker compose -p keep-local-stack -f $compose stop openldap }
        Write-Host 'Local identity processes and OpenLDAP stopped; credentials preserved.'
        return
    }
    if ($Action -eq 'Status') {
        Invoke-Checked { docker compose -p keep-local-stack -f $compose ps }
        foreach ($record in $script:records) { Write-Host "$($record.Role): $([bool](Get-OwnedProcess $record))" }
        return
    }
    if ($Action -eq 'Refresh') {
        # Apply edits to credentials.json users (OpenLDAP) and adapi-directory.json (the
        # mock), then publish once.
        Import-Environment
        Invoke-Checked { & $python scripts/local_identity.py configure }
        Start-Owned $adapi
        Invoke-Checked { & $python scripts/local_identity.py wait }
        Invoke-Checked { & $python scripts/local_identity.py reload }
        Invoke-DirectoryRefresh
        Write-Host 'Trusted local identity data refreshed once; no directory worker started.'
        return
    }
    # Retire only the directory process recorded and owned by this launcher.
    foreach ($record in @($script:records | Where-Object { $_.Role -eq 'Directory' })) { Stop-Owned $record }
    $script:records = @($script:records | Where-Object { $_.Role -ne 'Directory' })
    if (Test-Path -LiteralPath $processFile) { Save-Records }
    if ($Restart) {
        foreach ($record in $script:records) { Stop-Owned $record }
        $script:records = @()
    }
    Invoke-Checked { & $python scripts/local_identity.py prepare }
    Invoke-Checked { & $python scripts/local_v2.py prepare }
    Invoke-Checked { docker compose -p keep-local-runtime -f $compose stop kafka redis }
    Stop-LegacyIdentityContainers
    Invoke-Checked { docker compose -p keep-local-operators -f deploy/local/operators.compose.yaml stop }
    Invoke-Checked { docker compose -p keep-local-stack -f $compose up -d }
    Invoke-Checked { & $python scripts/local_identity.py database }
    # After migration 0070 and before the first ADAPI publication; a no-op once done.
    Invoke-Checked { & $python scripts/local_identity.py remap }
    Invoke-Checked { & $python scripts/local_identity.py environment }
    Import-Environment
    Start-Owned $adapi
    Invoke-Checked { & $python scripts/local_identity.py wait }
    Invoke-Checked { & $python scripts/local_identity.py configure }
    Invoke-DirectoryRefresh -Bootstrap
    $node = (Get-Command node).Source
    $definitions = @(
        @{ Role='Api'; File=$python; Directory=$repository; Arguments='-m uvicorn keep.main:create_app --factory --host 127.0.0.1 --port 58000 --no-access-log' },
        @{ Role='Ingestion'; File=$python; Directory=$repository; Arguments='-m uvicorn keep.ingestion.main:create_app --factory --host 127.0.0.1 --port 58001 --no-access-log' },
        @{ Role='Frontend'; File=$node; Directory=(Join-Path (Split-Path -Parent $repository) 'keep-frontend'); Arguments=('node_modules/next/dist/bin/next dev --hostname 127.0.0.1 --port 3443 --experimental-https --experimental-https-cert "' + (Join-Path $state 'tls/localhost.pem') + '" --experimental-https-key "' + (Join-Path $state 'tls/localhost-key.pem') + '"') }
    )
    foreach ($definition in $definitions) { Start-Owned $definition }
    $probe = @'
import ssl,time,httpx
from pathlib import Path
certificate=Path('.local/identity/tls/localhost.pem')
with httpx.Client(verify=ssl.create_default_context(cafile=str(certificate)),trust_env=False) as client:
    for attempt in range(60):
        try:
            api=client.get('http://127.0.0.1:58000/livez',timeout=2)
            ingestion=client.get('http://127.0.0.1:58001/livez',timeout=2)
            ui=client.get('https://localhost:3443/auth/session',timeout=3)
            if api.status_code==200 and ingestion.status_code==200 and ui.status_code==401:
                print('Product API, Ingestion API and HTTPS frontend are accepting requests.')
                break
        except httpx.HTTPError:
            pass
        time.sleep(1)
    else:
        raise SystemExit('API/ingestion/frontend startup failed; inspect .local/identity logs.')
'@
    Invoke-Checked { & $python -c $probe }
    foreach ($record in $script:records) { if (-not (Get-OwnedProcess $record)) { throw "$($record.Role) exited during startup; inspect its local log." } }
    Write-Host 'Keep: https://localhost:3443 | OpenLDAP: ldap://127.0.0.1:58389 | mock ADAPI: http://127.0.0.1:58092'
    Write-Host 'Local usernames and passwords: .local/identity/credentials.json'
} finally { Pop-Location }
