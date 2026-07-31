$ErrorActionPreference = "Stop"

$root     = Split-Path -Parent $PSScriptRoot
$launcher = Join-Path $PSScriptRoot "start_watchdog.ps1"
$taskName = "RadarV2Watchdog"
$userId   = "$env:USERDOMAIN\$env:USERNAME"

if (-not (Test-Path -LiteralPath $launcher)) {
    throw "Launcher nao encontrado em $launcher"
}

# Remove tarefa anterior (V1) se existir
Unregister-ScheduledTask -TaskName "RadarWatchdog" -Confirm:$false -ErrorAction SilentlyContinue

$action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$launcher`""

$triggers = @(
    (New-ScheduledTaskTrigger -AtLogOn -User $userId),
    (New-ScheduledTaskTrigger -AtStartup)
)

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -RestartCount 999 `
    -RestartInterval (New-TimeSpan -Minutes 1)

$principal = New-ScheduledTaskPrincipal -UserId $userId -LogonType Interactive

Register-ScheduledTask `
    -TaskName  $taskName `
    -Action    $action `
    -Trigger   $triggers `
    -Settings  $settings `
    -Principal $principal `
    -Force | Out-Null

Write-Host "Tarefa registrada: $taskName"
Write-Host "Launcher: $launcher"
Write-Host "Disparada em: logon de $userId e startup"
