# how it works

The technical side of tundra remote. For setup and usage, see [README.md](README.md).

## Pairing

`/host pair` hands out a one-liner that runs `scripts/install-remote.ps1`, served by the bot. It turns on the OpenSSH server (firewalled to `100.64.0.0/10`, the tailnet), authorizes the bot's key, downloads the probe scripts, installs `openvr`, registers the `TundraRemoteProbe` scheduled task, and reports back the SSH user, Python path and the sshd host key. The bot saves the PC in `data.json` with that host key pinned, so there's no `known_hosts` step. If `ssh_key` doesn't exist yet, the bot makes it first.

The bot only serves this while a code is waiting. It listens on its own tailnet IP and only answers the PC the code was made for, which it checks with `tailscale whois`. Each code works once and expires after 10 minutes.

`/host unpair` takes the bot's key off a remote PC and forgets it. On the bot's own PC it only hides it from `/status`. `/host update` copies `probe.py` and `poweroff.py` over SFTP.

## Why the scheduled task

OpenVR only answers programs in the logged-in user's desktop session, and SSH sessions aren't in it. So over SSH the bot runs `probe.py --via-task`, which starts `TundraRemoteProbe` (an on-demand task in the user's session). That task writes `%LOCALAPPDATA%\tundra-remote\probe.json`, and `--via-task` waits for the file and prints it. Turning devices off runs `lighthouse_console` straight from SSH, since it talks to the USB receivers directly.

When SteamVR is off, the probe asks `lighthouse_console` which receivers are plugged in instead, so `/status` can still list (and turn off) devices seen before on those receivers.

The remote PC's default SSH shell has to stay `cmd` (the Windows default).

## Pieces

| file | does |
| --- | --- |
| `tundra_remote/probe.py` | OpenVR snapshot: devices, battery, charging, receiver serial, SteamVR version |
| `tundra_remote/poweroff.py` | `lighthouse_console` poweroff by receiver serial (port of `scripts/tundra-off.ps1`) |
| `tundra_remote/hosts.py` | runs those locally, or over SSH via asyncssh; `/host update` and `unpair` over SFTP |
| `tundra_remote/pairing.py` | the `/host pair` setup server |
| `tundra_remote/tailnet.py` | `tailscale status` and `whois`: picking PCs, skipping offline ones, checking who's pairing |
| `tundra_remote/views.py` | the Components V2 `/status` layout |
| `tundra_remote/commands.py` | slash commands, permissions, button and dropdown handling |
| `tundra_remote/emojis.py` | uploads icons as application emojis |
| `tundra_remote/store.py` | `data.json`: nicknames, devices seen before, paired PCs |

`probe.py` and `poweroff.py` only use the standard library plus `openvr`, so they also run as plain scripts on the remote PCs.
