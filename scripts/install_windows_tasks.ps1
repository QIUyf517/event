[CmdletBinding()]
param(
    [string]$PrimaryTime = "18:30",
    [string]$RetryTime = "20:30"
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $projectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw "Project virtual environment not found: $pythonPath"
}

$identity = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$principal = New-ScheduledTaskPrincipal -UserId $identity -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -WakeToRun `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Hours 1)
$weekdays = @("Monday", "Tuesday", "Wednesday", "Thursday", "Friday")

$definitions = @(
    @{
        Name = "ETFResearchMonitor-Primary"
        Time = $PrimaryTime
        Slot = "primary"
        Description = "A-share ETF research: weekday 18:30 end-of-day sync and monitoring"
    },
    @{
        Name = "ETFResearchMonitor-Retry"
        Time = $RetryTime
        Slot = "retry"
        Description = "A-share ETF research: 20:30 conditional retry for incomplete data"
    }
)

foreach ($definition in $definitions) {
    $action = New-ScheduledTaskAction `
        -Execute $pythonPath `
        -Argument "-m app.cli --mode live scheduled-monitor --slot $($definition.Slot)" `
        -WorkingDirectory $projectRoot
    $trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek $weekdays -At $definition.Time
    $task = New-ScheduledTask -Action $action -Trigger $trigger -Settings $settings `
        -Principal $principal -Description $definition.Description
    Register-ScheduledTask -TaskName $definition.Name -InputObject $task -Force | Out-Null
}

Get-ScheduledTask -TaskName "ETFResearchMonitor-*" |
    Select-Object TaskName, State, @{Name="NextRunTime"; Expression={(Get-ScheduledTaskInfo $_).NextRunTime}}
