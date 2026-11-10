#Requires -Version 7
[CmdletBinding()]
param([ValidateSet('Start', 'Stop', 'Status')][string]$Action = 'Status', [switch]$Restart)
$ErrorActionPreference = 'Stop'
if ($Restart -and $Action -ne 'Start') { throw '-Restart requires Start.' }
$repository = Split-Path -Parent $PSScriptRoot
$state = Join-Path $repository '.local/operators'
$python = Join-Path $repository '.venv/Scripts/python.exe'
$compose = Join-Path $repository 'deploy/local/compose.yaml'
$recordFile = Join-Path $state 'processes.json'
function Checked([scriptblock]$Command) {
    & $Command
    if ($LASTEXITCODE -ne 0) { throw "Local v2 command failed (exit $LASTEXITCODE)." }
}
function Owned($record) {
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
    $records = if (Test-Path -LiteralPath $recordFile) { @(Get-Content -LiteralPath $recordFile -Raw | ConvertFrom-Json) } else { @() }
    if ($Action -eq 'Start' -and @($records | Where-Object { $_.Role -eq 'worker' -and (Owned $_) }).Count) { $Restart = $true }
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
        Checked { docker compose -p keep-local-stack -f $compose stop kong kong-db grafana }
        Write-Host 'Local v2 services stopped; all data preserved.'
        return
    }
    if ($Action -eq 'Status') {
        Checked { docker compose -p keep-local-stack -f $compose ps kong kong-db grafana }
        foreach ($record in $records) { Write-Host "$($record.Role): running=$([bool](Owned $record))" }
        return
    }
    Checked { & $python scripts/local_v2.py prepare }
    Checked { & $python scripts/local_identity.py prepare }
    Checked { docker compose -p keep-local-operators -f deploy/local/operators.compose.yaml stop }
    Checked { docker compose -p keep-local-stack -f $compose up -d }
    Checked { & $python scripts/local_v2.py configure }
    foreach ($role in @('private', 'proof')) {
        if (@($records | Where-Object { $_.Role -eq $role -and (Owned $_) }).Count) { continue }
        $process = Start-Process -FilePath $python -ArgumentList "scripts/local_v2.py $role" -WorkingDirectory $repository -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $state "$role.out.log") -RedirectStandardError (Join-Path $state "$role.err.log")
        $records = @($records | Where-Object { $_.Role -ne $role })
        $records += [pscustomobject]@{ Role=$role; Id=$process.Id; StartTime=$process.StartTime.ToUniversalTime().ToString('o') }
        $records | ConvertTo-Json -AsArray | Set-Content -LiteralPath $recordFile -Encoding utf8
    }
    Checked { & $python scripts/local_v2.py setup-operator }
    Checked { & $python scripts/local_v2_verify.py verify }
    Write-Host 'Verified local v2 gateway running at https://localhost:58445.'
} finally { Pop-Location }
