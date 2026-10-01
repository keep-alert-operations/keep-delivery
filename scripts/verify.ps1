[CmdletBinding()]
param(
    [ValidateSet('Unit', 'Build', 'Browser', 'Database', 'BrowserReal', 'All')]
    [string]$Suite = 'Unit',
    [ValidateSet('', 'chrome', 'msedge')][string]$BrowserChannel = ''
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$repository = Split-Path -Parent $PSScriptRoot
$backend = $repository
$frontend = Join-Path (Split-Path -Parent $repository) 'appchi-frontend'
$python = Join-Path $backend '.venv/Scripts/python.exe'
$npm = if (Get-Command npm.cmd -ErrorAction SilentlyContinue) { 'npm.cmd' } else { 'npm' }
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) { throw 'Install locked dependencies with scripts/dev.ps1 -Action Install first.' }

function Invoke-Checked([scriptblock]$Command, [string]$Description) {
    & $Command
    if ($LASTEXITCODE -ne 0) { throw "$Description failed (exit $LASTEXITCODE)." }
}

function Frontend-Command([string[]]$Arguments) {
    Push-Location -LiteralPath $frontend
    try { Invoke-Checked { & $npm @Arguments } 'Frontend verification' } finally { Pop-Location }
}

function Run-Suite([string]$Name) {
    Write-Host "Running $Name checks."
    switch ($Name) {
        'Unit' { Invoke-Checked { & $python scripts/workspace.py test } 'Backend unit suite' }
        'Build' {
            Frontend-Command @('run', 'format:check')
            Frontend-Command @('run', 'typecheck')
            Frontend-Command @('run', 'build')
        }
        'Database' {
            if ([string]::IsNullOrWhiteSpace($env:APPCHI_TEST_DATABASE_URL)) { throw 'Supply APPCHI_TEST_DATABASE_URL for an isolated test database; this suite applies migrations and writes synthetic records.' }
            Invoke-Checked { & $python -m pytest tests/integration -q --basetemp=.local/pytest-integration } 'Real PostgreSQL suite'
        }
        'Browser' {
            # Normal browser regression uses synthetic routes, even if a caller
            # previously opted in to the independently isolated real smoke.
            $originalAdmin = $env:APPCHI_REAL_BROWSER_DATABASE_ADMIN_URL
            try {
                [Environment]::SetEnvironmentVariable('APPCHI_REAL_BROWSER_DATABASE_ADMIN_URL', $null, 'Process')
                Frontend-Command @('test')
            } finally { [Environment]::SetEnvironmentVariable('APPCHI_REAL_BROWSER_DATABASE_ADMIN_URL', $originalAdmin, 'Process') }
        }
        'BrowserReal' {
            if ([string]::IsNullOrWhiteSpace($env:APPCHI_REAL_BROWSER_DATABASE_ADMIN_URL)) { throw 'Supply APPCHI_REAL_BROWSER_DATABASE_ADMIN_URL for a local isolated cluster with disposable-database create/drop permission.' }
            Frontend-Command @('test', '--', 'tests/integration-real.spec.ts')
        }
    }
}

$previousPythonPath = $env:PYTHONPATH
$previousChannel = $env:PLAYWRIGHT_CHANNEL
try {
    $env:PYTHONPATH = $null
    if ($BrowserChannel) { $env:PLAYWRIGHT_CHANNEL = $BrowserChannel }
    Push-Location -LiteralPath $repository
    try {
        & (Join-Path $PSScriptRoot 'dev.ps1') -Action Check
        if ($Suite -eq 'All') {
            foreach ($selected in @('Unit', 'Build', 'Browser')) { Run-Suite $selected }
            if ($env:APPCHI_TEST_DATABASE_URL) { Run-Suite 'Database' } else { Write-Host 'SKIPPED Database: APPCHI_TEST_DATABASE_URL was not supplied.' }
            if ($env:APPCHI_REAL_BROWSER_DATABASE_ADMIN_URL) { Run-Suite 'BrowserReal' } else { Write-Host 'SKIPPED BrowserReal: APPCHI_REAL_BROWSER_DATABASE_ADMIN_URL was not supplied.' }
        } else { Run-Suite $Suite }
        Write-Host 'Requested feasible checks passed. Skipped/private-environment checks remain unqualified.'
    } finally { Pop-Location }
} finally {
    [Environment]::SetEnvironmentVariable('PYTHONPATH', $previousPythonPath, 'Process')
    [Environment]::SetEnvironmentVariable('PLAYWRIGHT_CHANNEL', $previousChannel, 'Process')
}
