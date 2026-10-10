# tundra remote

A Discord bot for checking and turning off your SteamVR controllers and trackers, across every PC on your tailnet.

- `/status`: SteamVR version, every device with its battery, and a button per device. Click a controller or tracker to turn it off. If more than one PC is running SteamVR, a dropdown switches between them.
- `/device nick <device> <nickname>`: name a device ("left foot"). Leave the nickname empty to clear it.
- `/host nick <pc> <nickname>`: name a PC for the dropdown.
- `/host pair <pc> [name]`: set up another PC on your tailnet. See [Adding another PC](#adding-another-pc). Pick "this pc" (`local`) to add back the PC the bot runs on.
- `/host unpair <pc>`: forget a paired PC and take the bot's key off it. On the bot's own PC, this just hides it from `/status`, for when you don't play VR on it.
- `/host update [pc]`: copy this repo's `probe.py`/`poweroff.py` to the other PCs after you change or pull them.

Ops (`op_ids`) can do everything. Friends (`friend_ids`) can run `/status` and switch PCs, but can't turn anything off.

A device that's off can't be turned back on from here. Its radio is off, so pick it up and press the button.

## Setup (the PC running the bot)

1. Create an application at https://discord.com/developers/applications and add a bot. Under **Installation**, turn on both Guild Install and User Install. User install lets you run it in DMs and in servers it isn't in.
2. Install:
   ```
   python -m venv .venv
   .venv\Scripts\pip install -r requirements.txt
   ```
3. Copy `config.example.toml` to `config.toml`, then fill in the token and your Discord user id.
4. Run it with `.venv\Scripts\python -m tundra_remote`.

The PC running the bot shows up in `/status` as `this-pc`. Rename it with `/host nick`, or take it off with `/host unpair` if you don't play VR on it.

On first start, the bot uploads its status icons from `icons/` as application emojis (Developer Portal → your app → Emojis). These are Valve's and the driver makers' artwork. They're only visible through your own bot, but don't make the bot public without checking. If an upload fails, the buttons use plain emoji instead.

## Adding another PC

Each extra PC needs Python and Tailscale, and has to be signed in to the same tailnet. The bot PC needs the `tailscale` CLI. Nothing has to be started by hand afterwards.

1. In Discord, run `/host pair` and pick the PC from the list (Windows machines on your tailnet that aren't set up yet).
2. The bot replies with a one-liner like `irm http://100.x.y.z:8765/pair/K7Q2XM8P | iex`. On that PC, sign in as whoever plays VR there, open PowerShell as admin, and paste it.
3. That's it. The reply changes to "paired" and shows a test probe.

The one-liner runs `scripts/install-remote.ps1`, served by the bot. It turns on the OpenSSH server (firewalled to `100.64.0.0/10`, the tailnet), authorizes the bot's key, downloads the probe scripts, installs `openvr`, registers the `TundraRemoteProbe` scheduled task, and reports back the SSH user, Python path and the sshd host key. The bot saves the PC in `data.json` with that host key pinned, so there's no `known_hosts` step. If `ssh_key` doesn't exist yet, the bot makes it first.

The bot only serves this while a code is waiting. It listens on its own tailnet IP and only answers the PC the code was made for, which it checks with `tailscale whois`. Each code works once and expires after 10 minutes.

If the PC can't reach the bot (the request hangs), allow the port on the bot PC from an elevated PowerShell:
```
New-NetFirewallRule -Name TundraRemotePair -DisplayName 'tundra remote pairing (tailnet only)' -Direction Inbound -Protocol TCP -LocalPort 8765 -RemoteAddress 100.64.0.0/10 -Action Allow
```

### By hand

For a PC the bot can't serve to, copy this repo over and run `.\scripts\install-remote.ps1 -PublicKey 'path\to\tundra_remote.pub'` in an elevated PowerShell. Paste the `[[hosts]]` block it prints into `config.toml` on the bot PC, then run `ssh -i %USERPROFILE%\.ssh\tundra_remote you@thatpc` from the bot PC once to accept its host key.

### Why the scheduled task

OpenVR only answers programs in the logged-in user's desktop session, and SSH sessions aren't in it. So over SSH the bot runs `probe.py --via-task`, which starts `TundraRemoteProbe` (an on-demand task in the user's session). That task writes `%LOCALAPPDATA%\tundra-remote\probe.json`, and `--via-task` waits for the file and prints it. Turning devices off runs `lighthouse_console` straight from SSH, since it talks to the USB receivers directly. If nobody is logged in on that PC, it shows as not running.

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
