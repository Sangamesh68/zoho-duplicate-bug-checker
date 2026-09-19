<#
Start the Duplicate Bug Checker backend in the background.

Run it by hand any time (working late, weekend), or let the scheduled task
call it on weekday mornings. Safe to run twice: it exits quietly if the
backend is already up, so a manual start and the 10:00 task can't fight over
port 8000.

    .\scripts\start-backend.ps1
#>
[CmdletBinding()]
param(
    [int]$Port = 8000
)

$ErrorActionPreference = "Stop"

# Resolve the project root from this script's location, so the task doesn't
# depend on whatever directory it happens to be launched from.
$projectRoot = Split-Path -Parent $PSScriptRoot
# python.exe, not pythonw.exe: under pythonw there is no console, so sys.stdout
# is None and uvicorn's logger fails on startup. The window is hidden instead,
# and output goes to logs/ so an unattended 10:00 failure leaves a trace.
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
$logDir = Join-Path $projectRoot "logs"
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir | Out-Null }
$outLog = Join-Path $logDir "backend.out.log"
$errLog = Join-Path $logDir "backend.err.log"

if (-not (Test-Path $python)) {
    Write-Error "Python not found at $python. Has the virtualenv been created?"
    exit 1
}

# Already running? The command line is what identifies it — matching on
# process name alone would catch unrelated Python processes.
$existing = Get-CimInstance Win32_Process -Filter "Name = 'python.exe' OR Name = 'pythonw.exe'" |
    Where-Object { $_.CommandLine -match 'app\.main:app' }

if ($existing) {
    Write-Output "Backend already running (PID $($existing.ProcessId))."
    exit 0
}

Start-Process -FilePath $python `
    -ArgumentList "-m", "uvicorn", "app.main:app", "--port", "$Port" `
    -WorkingDirectory $projectRoot `
    -WindowStyle Hidden `
    -RedirectStandardOutput $outLog `
    -RedirectStandardError $errLog

# Give uvicorn a moment to bind, then confirm rather than assume.
Start-Sleep -Seconds 12
try {
    $health = Invoke-WebRequest "http://localhost:$Port/api/health" -UseBasicParsing -TimeoutSec 20
    Write-Output "Backend started: $($health.Content)"
} catch {
    Write-Warning "Backend did not answer on port $Port. See $errLog"
    exit 1
}
