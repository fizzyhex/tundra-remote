<#
.SYNOPSIS
  One-time setup so the tundra remote bot can reach this PC over Tailscale.

.DESCRIPTION
  Run from an elevated PowerShell on each PC that isn't hosting the bot, signed in as the
  user who plays VR. It:
    1. installs and enables the Windows OpenSSH server, reachable only from the tailnet
    2. authorizes the bot's public key
    3. copies the probe/poweroff scripts and installs pyopenvr
    4. registers the TundraRemoteProbe task, which only runs when the bot asks for it

  Nothing stays running in the background apart from the sshd service.

.EXAMPLE
  .\install-remote.ps1 -PublicKey 'C:\path\to\tundra_remote.pub'
#>
param(
    [Parameter(Mandatory)] [string]$PublicKey,   # the bot's public key, as a path or the key text
    [string]$Python = (Get-Command python -ErrorAction SilentlyContinue).Source
)

$ErrorActionPreference = 'Stop'

$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) { throw 'run this from an elevated powershell' }
if (-not $Python) { throw 'python not found. install it, or pass -Python C:\path\to\python.exe' }
$Pythonw = Join-Path (Split-Path $Python) 'pythonw.exe'
if (-not (Test-Path $Pythonw)) { $Pythonw = $Python }

# 1. OpenSSH server, firewalled to the tailnet (100.64.0.0/10)
if ((Get-WindowsCapability -Online -Name 'OpenSSH.Server*').State -ne 'Installed') {
    Write-Host 'installing openssh server...'
    Add-WindowsCapability -Online -Name 'OpenSSH.Server~~~~0.0.1.0' | Out-Null
}
Set-Service sshd -StartupType Automatic
Start-Service sshd
Get-NetFirewallRule -Name 'OpenSSH-Server-In-TCP' -ErrorAction SilentlyContinue | Remove-NetFirewallRule
New-NetFirewallRule -Name 'OpenSSH-Server-In-TCP' -DisplayName 'OpenSSH Server (tailnet only)' `
    -Direction Inbound -Protocol TCP -LocalPort 22 -RemoteAddress '100.64.0.0/10' -Action Allow | Out-Null

# 2. authorize the bot's key. Admin accounts use the shared administrators_authorized_keys file.
$key = if (Test-Path $PublicKey) { (Get-Content $PublicKey -Raw).Trim() } else { $PublicKey.Trim() }
$isAdminUser = (Get-LocalGroupMember -SID 'S-1-5-32-544' -ErrorAction SilentlyContinue).Name -contains "$env:USERDOMAIN\$env:USERNAME"
if ($isAdminUser) {
    $authFile = Join-Path $env:ProgramData 'ssh\administrators_authorized_keys'
} else {
    $authFile = Join-Path $env:USERPROFILE '.ssh\authorized_keys'
    New-Item -ItemType Directory -Force (Split-Path $authFile) | Out-Null
}
if (-not (Test-Path $authFile) -or -not (Select-String -Path $authFile -SimpleMatch $key -Quiet)) {
    Add-Content -Path $authFile -Value $key -Encoding ascii
}
if ($isAdminUser) { icacls $authFile /inheritance:r /grant 'Administrators:F' /grant 'SYSTEM:F' | Out-Null }

# 3. scripts + pyopenvr
$app = Join-Path $env:LOCALAPPDATA 'tundra-remote\app\tundra_remote'
New-Item -ItemType Directory -Force $app | Out-Null
$src = Join-Path $PSScriptRoot '..\tundra_remote'
foreach ($f in '__init__.py', 'probe.py', 'poweroff.py') { Copy-Item (Join-Path $src $f) $app -Force }
& $Python -m pip install --user --quiet openvr
if ($LASTEXITCODE -ne 0) { throw 'pip install openvr failed' }

# 4. on-demand probe task in the user's desktop session, where OpenVR is reachable
$action = New-ScheduledTaskAction -Execute $Pythonw -Argument "`"$app\probe.py`" --file"
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 1) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName 'TundraRemoteProbe' -Action $action -Principal $principal -Settings $settings -Force | Out-Null

$ip = try { & tailscale ip -4 2>$null | Select-Object -First 1 } catch { $null }
Write-Host ''
Write-Host 'done. add this to config.toml on the bot pc:'
Write-Host ''
Write-Host '[[hosts]]'
Write-Host "name = `"$($env:COMPUTERNAME.ToLower())`""
Write-Host "tailnet_host = `"$(if ($ip) { $ip } else { $env:COMPUTERNAME.ToLower() })`""
Write-Host "ssh_user = `"$env:USERNAME`""
Write-Host "python = '$Python'"
Write-Host "remote_dir = '$(Split-Path $app)'"
Write-Host ''
Write-Host 'then ssh into it once from the bot pc so its host key lands in known_hosts.'
