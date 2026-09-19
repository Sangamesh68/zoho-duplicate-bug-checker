<#
Stop the Duplicate Bug Checker backend.

Called by the 19:00 scheduled task, and usable by hand.

    .\scripts\stop-backend.ps1
#>
[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"

# Match on the command line, never just the process name: killing every
# pythonw.exe would take down unrelated work.
$running = Get-CimInstance Win32_Process -Filter "Name = 'pythonw.exe' OR Name = 'python.exe'" |
    Where-Object { $_.CommandLine -match 'app\.main:app' }

if (-not $running) {
    Write-Output "Backend is not running."
    exit 0
}

foreach ($proc in $running) {
    Stop-Process -Id $proc.ProcessId -Force
    Write-Output "Stopped backend (PID $($proc.ProcessId))."
}
