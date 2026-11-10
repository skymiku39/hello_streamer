<##
Launch an installed portable copy independently of the invoking terminal's
Windows job. This avoids losing the monitor when a development host updates
or shuts down. No scheduled task, service, or startup setting is installed.
##>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$AppDirectory,
    [switch]$ResumeMonitoring
)

$ErrorActionPreference = 'Stop'
$appRoot = (Resolve-Path -LiteralPath $AppDirectory).Path
$appExecutable = Join-Path $appRoot 'HelloStreamer.exe'
if (-not (Test-Path -LiteralPath $appExecutable -PathType Leaf)) {
    throw "Missing portable executable: $appExecutable"
}
if ($appExecutable.Contains('"')) { throw 'Invalid executable path' }
$commandLine = '"' + $appExecutable + '"'
if ($ResumeMonitoring) { $commandLine += ' --silent' }

# Local WMI creates the process outside the caller's job. ShowWindow=0
# keeps startup hidden; the app retains its saved tray/visibility behavior.
$startup = New-CimInstance -ClassName Win32_ProcessStartup -ClientOnly -Property @{ ShowWindow = [uint16]0 }
$result = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{
    CommandLine = $commandLine
    CurrentDirectory = $appRoot
    ProcessStartupInformation = $startup
}
if ($result.ReturnValue -ne 0) {
    throw "Portable launch failed: Win32_Process.Create returned $($result.ReturnValue)"
}
[pscustomobject]@{ ProcessId = $result.ProcessId; Executable = $appExecutable; ResumeMonitoring = [bool]$ResumeMonitoring }
