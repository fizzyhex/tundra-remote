<#
.SYNOPSIS
  Powers off Tundra (or other Watchman-based) trackers without needing the SteamVR UI.

.DESCRIPTION
  Uses SteamVR's bundled lighthouse_console.exe to talk to the USB dongle receivers
  directly, find which ones have a tracker connected, and send each one "poweroff".

.EXAMPLE
  .\tundra-off.ps1                 # power off every connected tracker
  .\tundra-off.ps1 -List           # just show what's connected
  .\tundra-off.ps1 -Serial LHR-E4CA8D43
#>
param(
    [string[]]$Serial,             # only power off these tracker serials
    [switch]$List,                 # list connected trackers, don't power anything off
    [switch]$IncludeControllers    # also scan the headset's built-in controller receivers (-LYX / -RYB)
)

$ErrorActionPreference = 'Stop'

function Find-LighthouseConsole {
    $rel = 'steamapps\common\SteamVR\tools\lighthouse\bin\win64\lighthouse_console.exe'
    $steam = (Get-ItemProperty 'HKCU:\Software\Valve\Steam' -ErrorAction SilentlyContinue).SteamPath
    if (-not $steam) { $steam = 'C:\Program Files (x86)\Steam' }

    $libs = @($steam)
    $vdf = Join-Path $steam 'steamapps\libraryfolders.vdf'
    if (Test-Path $vdf) {
        $libs += Select-String -Path $vdf -Pattern '"path"\s+"(.+)"' |
            ForEach-Object { $_.Matches[0].Groups[1].Value -replace '\\\\', '\' }
    }
    foreach ($lib in $libs) {
        $p = Join-Path $lib $rel
        if (Test-Path $p) { return $p }
    }
    throw "lighthouse_console.exe not found. Is SteamVR installed?"
}

# Runs lighthouse_console with the given commands on stdin and returns its output.
function Invoke-Lighthouse([string]$exe, [string[]]$commands) {
    $psi = New-Object System.Diagnostics.ProcessStartInfo $exe
    $psi.UseShellExecute = $false
    $psi.RedirectStandardInput = $true
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.CreateNoWindow = $true
    $proc = [System.Diagnostics.Process]::Start($psi)
    $out = $proc.StandardOutput.ReadToEndAsync()
    $err = $proc.StandardError.ReadToEndAsync()
    # lighthouse_console wants bare LF line endings. The leading blank line soaks up the
    # byte-order mark .NET may prepend; otherwise the first command (maybe "quit") is garbled
    # and the console spins forever on EOF.
    $proc.StandardInput.Write("`n" + (($commands + 'quit') -join "`n") + "`n")
    $proc.StandardInput.Close()
    if (-not $proc.WaitForExit(60000)) { $proc.Kill(); throw "lighthouse_console timed out" }
    return $out.Result + $err.Result
}

$lc = Find-LighthouseConsole

# 1. Enumerate receivers (USB dongles + headset-internal receivers)
$receivers = @()
$inList = $false
foreach ($line in (Invoke-Lighthouse $lc @()) -split "`r?`n") {
    if ($line -match '^Attached lighthouse receiver devices') { $inList = $true; continue }
    if ($inList) {
        if ($line -match '^\t(\S+)') { $receivers += $Matches[1] } else { break }
    }
}
# The headset itself shows up as an LHR- receiver; controller links end in -LYX / -RYB
$receivers = $receivers | Where-Object { $_ -notlike 'LHR-*' }
if (-not $IncludeControllers) { $receivers = $receivers | Where-Object { $_ -notmatch '-(LYX|RYB)$' } }

# 2. Find which receivers have a tracker connected
$scan = foreach ($r in $receivers) { "serial $r"; 'sleep 1500' }
$trackers = [ordered]@{}
foreach ($m in [regex]::Matches((Invoke-Lighthouse $lc $scan), '(?m)^(LHR-[0-9A-F]+): Connected to receiver (\S+)')) {
    $trackers[$m.Groups[1].Value] = $m.Groups[2].Value
}
if ($Serial) {
    $wanted = [ordered]@{}
    foreach ($s in $Serial) {
        if ($trackers.Contains($s)) { $wanted[$s] = $trackers[$s] } else { Write-Warning "$s is not connected" }
    }
    $trackers = $wanted
}

if ($trackers.Count -eq 0) { Write-Host 'No connected trackers found (already off?).'; return }

foreach ($t in $trackers.Keys) { Write-Host "Found $t (receiver $($trackers[$t]))" }
if ($List) { return }

# 3. Power them off
$cmds = foreach ($t in $trackers.Keys) { "serial $($trackers[$t])"; 'sleep 1500'; 'poweroff'; 'sleep 500' }
Invoke-Lighthouse $lc $cmds | Out-Null
Write-Host "Sent power-off to $($trackers.Count) tracker(s)."
