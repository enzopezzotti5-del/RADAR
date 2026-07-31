$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot
$launcher = Join-Path $root 'radar_v2\start_watchdog.ps1'

if (-not (Test-Path -LiteralPath $launcher)) {
    throw "Launcher do Radar nao encontrado: $launcher"
}

# The watchdog owns the server process and writes the operational logs below
# <root>\logs. Do not start a second direct run_server.py instance.
& $launcher
