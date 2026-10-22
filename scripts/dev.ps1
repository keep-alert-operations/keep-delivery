#Requires -Version 7
[CmdletBinding()]
param(
    [ValidateSet('Install', 'Check', 'Migrate', 'Start')]
    [string]$Action = 'Check',
    [ValidateSet('Api', 'Worker', 'Frontend')]
    [string]$Role = 'Api',
    [ValidateSet('source', 'relay', 'indexer', 'maintenance-warmup', 'maintenance-proof', 'correlation', 'protected-deletions', 'visibility-capture', 'source-hints', 'indexer-hints', 'correlation-hints')]
    [string]$WorkerRole = 'source',
    [string]$TenantId,
    [ValidateRange(1024, 65535)][int]$ApiPort = 8000,
    [ValidateRange(1024, 65535)][int]$FrontendPort = 3443,
    [string]$TlsCertificate = $env:APPCHI_DEV_TLS_CERTIFICATE,
    [string]$TlsKey = $env:APPCHI_DEV_TLS_KEY,
    [switch]$CheckConfiguration,
    [switch]$RequireIdentityConfiguration,
    [switch]$Once
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$repository = Split-Path -Parent $PSScriptRoot
$backend = $repository
$frontend = Join-Path (Split-Path -Parent $repository) 'appchi-frontend'
$python = Join-Path $backend '.venv/Scripts/python.exe'
$nodeCommand = Get-Command node -ErrorAction SilentlyContinue
$bundledNpm = if ($nodeCommand) { Join-Path (Split-Path -Parent $nodeCommand.Source) 'npm.cmd' } else { $null }
$npm = if ($bundledNpm -and (Test-Path -LiteralPath $bundledNpm -PathType Leaf)) { $bundledNpm } elseif (Get-Command npm.cmd -ErrorAction SilentlyContinue) { (Get-Command npm.cmd).Source } else { 'npm' }

function Invoke-Checked([scriptblock]$Command, [string]$Description) {
    & $Command
    if ($LASTEXITCODE -ne 0) { throw "$Description failed (exit $LASTEXITCODE)." }
}

function Require-Environment([string[]]$Names) {
    $missing = @($Names | Where-Object { [string]::IsNullOrWhiteSpace([Environment]::GetEnvironmentVariable($_, 'Process')) })
    if ($missing.Count -gt 0) { throw ('Supply environment variables: ' + ($missing -join ', ')) }
}

function Require-Python {
    if (-not (Test-Path -LiteralPath $python -PathType Leaf)) { throw 'Backend virtual environment missing. Run dev.ps1 -Action Install first.' }
}

function Validate-Configuration {
    Require-Environment @('APPCHI_DATABASE_URL', 'APPCHI_DEPLOYMENT_EPOCH', 'APPCHI_PUBLIC_ORIGIN')
    if (-not $env:APPCHI_WRAPPING_KEY -and -not $env:APPCHI_WRAPPING_KEYRING) { throw 'Supply APPCHI_WRAPPING_KEYRING or APPCHI_WRAPPING_KEY.' }
    if ($RequireIdentityConfiguration) {
        Require-Environment @('APPCHI_OIDC_ISSUER', 'APPCHI_OIDC_CLIENT_SECRET', 'APPCHI_OIDC_DIRECTORY_SOURCE', 'APPCHI_OIDC_DIRECTORY_ID_CLAIM', 'APPCHI_OIDC_DIRECTORY_SCOPE', 'APPCHI_OIDC_REQUIRED_ACR', 'APPCHI_OIDC_REQUIRED_AMR', 'APPCHI_SUPERADMIN_GROUP')
    }
    $validation = @'
import sys
from appchi.config import Settings
from psycopg.conninfo import conninfo_to_dict
try:
    settings=Settings()
    conninfo_to_dict(settings.database_url.get_secret_value())
except Exception as error:
    print('Configuration validation failed ('+type(error).__name__+'); values are intentionally not printed.', file=sys.stderr)
    sys.exit(1)
print('Configuration schema validated; service connectivity and readiness were not checked.')
'@
    Invoke-Checked { & $python -c $validation } 'Configuration validation'
}

$previousPythonPath = $env:PYTHONPATH
try {
    $env:PYTHONPATH = $null
    Push-Location -LiteralPath $repository
    try {
        switch ($Action) {
            'Install' {
                if (-not (Get-Command poetry -ErrorAction SilentlyContinue)) { throw 'Install Poetry 2.3.1 and Python 3.13 through your approved development tooling.' }
                if (-not (Get-Command node -ErrorAction SilentlyContinue)) { throw 'Install Node.js 22.17 or newer through your approved development tooling.' }
                Invoke-Checked { & python scripts/workspace.py install --editable } 'Install workspace dependencies'
                Write-Host 'Dependencies installed from committed locks. No services, migrations or identity fixtures were started.'
            }
            'Check' {
                Require-Python
                Invoke-Checked { & $python -c 'import sys; assert sys.version_info[:2] == (3,13), "Python 3.13 required"; import appchi.main, appchi.worker; print("Backend imports and Python version passed")' } 'Backend import check'
                Invoke-Checked { & node -e 'const [major,minor]=process.versions.node.split(".").map(Number); if(major<22||(major===22&&minor<17))process.exit(1);require(process.argv[1]+"/node_modules/next/package.json");console.log("Frontend dependency and Node version passed")' $frontend } 'Frontend dependency check'
                if ($CheckConfiguration -or $RequireIdentityConfiguration) { Validate-Configuration }
            }
            'Migrate' {
                Require-Python
                Require-Environment @('APPCHI_MIGRATION_URL')
                $migration = @'
import sys
from appchi.migrate import main
try:
    main()
except Exception as error:
    print('Migration failed ('+type(error).__name__+'); use secured diagnostics without printing the DSN.', file=sys.stderr)
    sys.exit(1)
print('Ordered checksum-verified migrations applied.')
'@
                Invoke-Checked { & $python -c $migration } 'Migration'
            }
            'Start' {
                if ($Role -eq 'Frontend') {
                    Require-Environment @('APPCHI_BACKEND_URL', 'APPCHI_PUBLIC_ORIGIN')
                    $binding = [Uri]$env:APPCHI_BACKEND_URL
                    if (-not $binding.IsAbsoluteUri -or $binding.Scheme -notin @('http', 'https') -or $binding.UserInfo -or $binding.AbsolutePath -ne '/' -or $binding.Query -or $binding.Fragment) { throw 'APPCHI_BACKEND_URL must be a trusted absolute HTTP(S) origin without credentials, path, query or fragment.' }
                    $public = [Uri]$env:APPCHI_PUBLIC_ORIGIN
                    if (-not $public.IsAbsoluteUri -or $public.Scheme -ne 'https' -or $public.Port -ne $FrontendPort -or $public.Host -notin @('localhost', '127.0.0.1') -or $public.UserInfo -or $env:APPCHI_PUBLIC_ORIGIN -cne $public.GetLeftPart([UriPartial]::Authority)) { throw 'Local frontend requires an exact HTTPS localhost/127.0.0.1 APPCHI_PUBLIC_ORIGIN matching FrontendPort, without a path, trailing slash, query or credentials.' }
                    if (-not $TlsCertificate -or -not $TlsKey -or -not (Test-Path -LiteralPath $TlsCertificate -PathType Leaf) -or -not (Test-Path -LiteralPath $TlsKey -PathType Leaf)) { throw 'Supply a trusted local TLS certificate and private key using parameters or APPCHI_DEV_TLS_CERTIFICATE / APPCHI_DEV_TLS_KEY.' }
                    $certificatePath = (Resolve-Path -LiteralPath $TlsCertificate).Path
                    $keyPath = (Resolve-Path -LiteralPath $TlsKey).Path
                    Push-Location -LiteralPath $frontend
                    try {
                        Write-Host 'Starting the HTTPS frontend in this terminal. Ctrl+C stops it; backend routing uses supplied configuration.'
                        Invoke-Checked { & node 'node_modules/next/dist/bin/next' dev --hostname 127.0.0.1 --port $FrontendPort --experimental-https --experimental-https-cert $certificatePath --experimental-https-key $keyPath } 'Frontend'
                    } finally { Pop-Location }
                } else {
                    Require-Python
                    Validate-Configuration
                    if ($Role -eq 'Api') {
                        Write-Host 'Starting the loopback Product API in this terminal. Ctrl+C stops it.'
                        Invoke-Checked { & $python -m uvicorn appchi.main:create_app --factory --host 127.0.0.1 --port $ApiPort --no-access-log } 'Product API'
                    } else {
                        if ($WorkerRole -eq 'relay') { Require-Environment @('APPCHI_KAFKA_BOOTSTRAP') }
                        if ($WorkerRole -eq 'indexer') { Require-Environment @('APPCHI_ELASTICSEARCH_URL') }
                        $modules = @{ source='appchi.processor.main'; correlation='appchi.correlation.main'; indexer='appchi.search.main' }
                        $workerArguments = if ($modules.ContainsKey($WorkerRole)) { @('-m', $modules[$WorkerRole]) } else { @('-m', 'appchi.worker', $WorkerRole) }
                        if ($TenantId -and $modules.ContainsKey($WorkerRole)) { throw 'Kafka pipeline workers consume their assigned partitions; --tenant applies only to explicit helpers.' }
                        if ($TenantId) { $workerArguments += @('--tenant', $TenantId) }
                        if ($Once) { $workerArguments += '--once' }
                        Write-Host "Starting the $WorkerRole worker in this terminal. A successful process exit does not qualify an integration gate."
                        Invoke-Checked { & $python @workerArguments } 'Worker'
                    }
                }
            }
        }
    } finally { Pop-Location }
} finally {
    [Environment]::SetEnvironmentVariable('PYTHONPATH', $previousPythonPath, 'Process')
}
