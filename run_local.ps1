# run_local.ps1 — run PlacementRadar OFFLINE on this machine.
#
# The app reads its config from environment variables (not from a file), so this
# script loads placementradar.env into the environment and then starts the app.
#
# Usage (from this folder):
#     .\run_local.ps1
# If PowerShell blocks the script, allow it for this session first:
#     Set-ExecutionPolicy -Scope Process -Bypass
#
# Then open http://localhost:5000 and log in with a CRM account.

$ErrorActionPreference = 'Stop'
$envFile = Join-Path $PSScriptRoot 'placementradar.env'

if (-not (Test-Path $envFile)) {
    Write-Host "ERROR: placementradar.env not found next to this script." -ForegroundColor Red
    exit 1
}

# Load KEY=VALUE lines (ignoring blanks and # comments) into this process's env.
Get-Content $envFile | ForEach-Object {
    $line = $_.Trim()
    if ($line -and -not $line.StartsWith('#')) {
        $idx = $line.IndexOf('=')
        if ($idx -gt 0) {
            $name = $line.Substring(0, $idx).Trim()
            $val  = $line.Substring($idx + 1).Trim()
            # strip a single pair of surrounding quotes if present
            if ($val.Length -ge 2 -and
                (($val[0] -eq "'" -and $val[$val.Length-1] -eq "'") -or
                 ($val[0] -eq '"' -and $val[$val.Length-1] -eq '"'))) {
                $val = $val.Substring(1, $val.Length - 2)
            }
            [Environment]::SetEnvironmentVariable($name, $val, 'Process')
        }
    }
}

Write-Host "Loaded placementradar.env" -ForegroundColor Green
Write-Host ("  MONGO_URI db      : " + ($env:MONGO_URI      -replace '.*/', ''))
Write-Host ("  AUTH_MONGO_URI db : " + ($env:AUTH_MONGO_URI -replace '.*/', ''))
Write-Host ("  SESSION_COOKIE_SECURE = " + $env:SESSION_COOKIE_SECURE + "  (0 = correct for local http)")
Write-Host ""
Write-Host "Starting PlacementRadar -> http://localhost:5000  (Ctrl+C to stop)" -ForegroundColor Cyan
python app.py
