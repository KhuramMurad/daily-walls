# Explicit opt-in only. Run from PowerShell as the user who runs Daily Walls.
param(
    [switch]$Remove,
    [string]$Executable = (Join-Path $PSScriptRoot 'DailyWalls.exe')
)
$ErrorActionPreference = 'Stop'
$taskName = 'Daily Walls Windows Maintenance'
if ($Remove) {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Output 'Daily Walls maintenance disabled.'
    exit 0
}
$exe = (Resolve-Path -LiteralPath $Executable).Path
$identity = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$action = New-ScheduledTaskAction -Execute $exe -Argument '--maintain' -WorkingDirectory (Split-Path $exe)
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Hours 1)
$principal = New-ScheduledTaskPrincipal -UserId $identity -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 20)
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
Write-Output 'Hourly maintenance enabled for your signed-in Windows session.'
