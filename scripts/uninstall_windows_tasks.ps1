[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$taskNames = @("ETFResearchMonitor-Primary", "ETFResearchMonitor-Retry")
foreach ($taskName in $taskNames) {
    $task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if ($task) {
        Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
    }
}

Write-Output "ETF research scheduled tasks removed. Database and history were preserved."
