# Windows entry point: finds a supported Python and runs setup_dev.py.
# Usage: powershell -ExecutionPolicy Bypass -File setup.ps1 [options]
# Options are passed through, e.g. --provision-db, --recreate-venv, --help.

$ErrorActionPreference = 'Continue'
$setupScript = Join-Path $PSScriptRoot 'setup_dev.py'
$versionTest = 'import sys; sys.exit(0 if (3, 12) <= sys.version_info[:2] <= (3, 14) else 1)'

$candidates = @(
    @('py', '-3.13'), @('py', '-3.12'), @('py', '-3.14'), @('python'), @('python3')
)

foreach ($candidate in $candidates) {
    $exe = $candidate[0]
    $prefix = @($candidate | Select-Object -Skip 1)
    if (-not (Get-Command $exe -ErrorAction SilentlyContinue)) { continue }
    & $exe @prefix -c $versionTest 2>$null
    if ($LASTEXITCODE -eq 0) {
        & $exe @prefix $setupScript @args
        exit $LASTEXITCODE
    }
}

Write-Host 'ERROR: Python 3.12 or 3.13 was not found.' -ForegroundColor Red
Write-Host 'Install Python 3.13 from https://www.python.org/downloads/ (include the py launcher), then re-run.'
exit 1
