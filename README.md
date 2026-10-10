# tundra remote

```sh
.venv\Scripts\python -m tundra_remote
```

A Discord bot for checking and turning off your SteamVR controllers and trackers, across every PC on your tailnet.

- `/status`: every device with its battery. Click a controller or tracker to turn it off. If more than one PC is running SteamVR, a dropdown switches between them.
- `/device nick <device> <nickname>`: name a device ("left foot"). Leave the nickname empty to clear it.
- `/host nick <pc> <nickname>`: name a PC for the dropdown.
- `/host pair <pc> [name]`: add another PC. See [Adding another PC](#adding-another-pc).
- `/host unpair <pc>`: remove a PC.
- `/host update [pc]`: send the bot's latest scripts to your other PCs. Run it after updating the bot.

A device that's off can't be turned back on from here. Its radio is off, so pick it up and press the button.

## Setup (the PC running the bot)

You'll need Python and Tailscale on this PC!

1. Create an application at https://discord.com/developers/applications and add a bot. Under **Installation**, turn on both Guild Install and User Install, so you can use it in DMs and in servers it isn't in.
2. Install:
   ```
   python -m venv .venv
   .venv\Scripts\pip install -r requirements.txt
   ```
3. Copy `config.example.toml` to `config.toml`, then fill in the token and your Discord user id under `op_ids`. Ops can do everything. Add anyone who should only look (no turning things off) to `friend_ids`.
4. Run it with `.venv\Scripts\python -m tundra_remote`.

This PC shows up in `/status` as `this-pc`. If you don't play VR on it, hide it with `/host unpair`, and bring it back with `/host pair` → "this pc".

The status icons are Valve's and the driver makers' artwork. They're only visible through your own bot, but don't make the bot public without checking.

## Adding another PC

The PC needs Python and Tailscale, signed in to the same tailnet.

1. In Discord, run `/host pair` and pick the PC from the list.
2. The bot replies with a one-liner like `irm http://100.x.y.z:8765/pair/K7Q2XM8P | iex`. On that PC, sign in as whoever plays VR there, open PowerShell as admin, and paste it within 10 minutes.
3. That's it. The reply changes to "paired" and shows that PC's devices.

### By hand

If the one-liner won't work on a PC, copy this repo over and run `.\scripts\install-remote.ps1 -PublicKey 'path\to\tundra_remote.pub'` in an elevated PowerShell. Paste the `[[hosts]]` block it prints into `config.toml` on the bot PC, then run `ssh -i %USERPROFILE%\.ssh\tundra_remote you@thatpc` from the bot PC once to accept its host key.

## Troubleshooting

- **The one-liner hangs:** allow the pairing port on the bot PC, from an elevated PowerShell:
  ```
  New-NetFirewallRule -Name TundraRemotePair -DisplayName 'tundra remote pairing (tailnet only)' -Direction Inbound -Protocol TCP -LocalPort 8765 -RemoteAddress 100.64.0.0/10 -Action Allow
  ```
- **A PC shows as not running:** someone has to be logged in on it.
- **SteamVR is closed but a tracker's still on:** `/status` still lists devices it's seen before on that PC, so you can turn it off.
- **Buttons show plain emoji:** the bot couldn't upload its icons. Check Developer Portal → your app → Emojis.

How it all works under the hood is in [details.md](details.md).

## Thanks

- `pyopenvr` - for probing steamvr
- `discord.py` - for wrapping the discord api!