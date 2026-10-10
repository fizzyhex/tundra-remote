# tundra remote

A Discord bot for checking and turning off your SteamVR controllers and trackers, across every PC on your tailnet.

- `/status`: SteamVR version, every device with its battery, and a button per device. Click a controller or tracker to turn it off. If more than one PC is running SteamVR, a dropdown switches between them.
- `/device nick <device> <nickname>`: name a device ("left foot"). Leave the nickname empty to clear it.
- `/host nick <pc> <nickname>`: name a PC for the dropdown.

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

On first start, the bot uploads its status icons from `icons/` as application emojis (Developer Portal → your app → Emojis). These are Valve's and the driver makers' artwork. They're only visible through your own bot, but don't make the bot public without checking. If an upload fails, the buttons use plain emoji instead.

## Adding another PC

Each extra PC needs Python, Tailscale, and a one-time setup. Nothing has to be started by hand afterwards.

1. On the bot PC, make a key: `ssh-keygen -t ed25519 -f %USERPROFILE%\.ssh\tundra_remote -N ""` and set `ssh_key` in `config.toml`.
2. Copy this repo to the other PC. In an elevated PowerShell:
   ```
   .\scripts\install-remote.ps1 -PublicKey 'path\to\tundra_remote.pub'
   ```
   This turns on the OpenSSH server (firewalled to `100.64.0.0/10`, the tailnet), authorizes the key, copies the probe scripts, installs `openvr`, and registers the `TundraRemoteProbe` scheduled task.
3. Paste the `[[hosts]]` block it prints into `config.toml` on the bot PC.
4. From the bot PC, run `ssh -i %USERPROFILE%\.ssh\tundra_remote you@thatpc` once to accept its host key.

### Why the scheduled task

OpenVR only answers programs in the logged-in user's desktop session, and SSH sessions aren't in it. So over SSH the bot runs `probe.py --via-task`, which starts `TundraRemoteProbe` (an on-demand task in the user's session). That task writes `%LOCALAPPDATA%\tundra-remote\probe.json`, and `--via-task` waits for the file and prints it. Turning devices off runs `lighthouse_console` straight from SSH, since it talks to the USB receivers directly. If nobody is logged in on that PC, it shows as not running.

The remote PC's default SSH shell has to stay `cmd` (the Windows default).

## Pieces

| file | does |
| --- | --- |
| `tundra_remote/probe.py` | OpenVR snapshot: devices, battery, charging, receiver serial, SteamVR version |
| `tundra_remote/poweroff.py` | `lighthouse_console` poweroff by receiver serial (port of `scripts/tundra-off.ps1`) |
| `tundra_remote/hosts.py` | runs those locally, or over SSH via asyncssh |
| `tundra_remote/views.py` | the Components V2 `/status` layout |
| `tundra_remote/commands.py` | slash commands, permissions, button and dropdown handling |
| `tundra_remote/emojis.py` | uploads icons as application emojis |
| `tundra_remote/store.py` | `data.json`: nicknames, devices seen before, emoji ids |

`probe.py` and `poweroff.py` only use the standard library plus `openvr`, so they also run as plain scripts on the remote PCs.
