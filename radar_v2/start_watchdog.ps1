$ErrorActionPreference = "Stop"

$root   = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"
$script = Join-Path $PSScriptRoot "run_watchdog.py"

if (-not (Test-Path -LiteralPath $python)) {
    throw "Python da .venv nao encontrado em $python"
}

Start-Process -FilePath $python `
    -ArgumentList @($script) `
    -WorkingDirectory $root `
    -WindowStyle Hidden
