# Agent notes

## Icons

`icons/` holds icons copied from the local SteamVR install. Use them instead of making new ones.

- `icons/steamvr/`: generic device status icons (`{headset,controller,tracker,base,other}_status_<state>`)
- `icons/tundra_labs/`: Tundra tracker icons, most with `_2x` variants
- `icons/<driver>/`: other drivers' icons (`lighthouse`, `htc`, `indexhmd`, `indexcontroller`, `oculus`, `vrlink`, `slimevr`, `frame_controller`, `gamepad`)
- `icons/battery/vrmonitor/`: `battery_status_{empty,low,1-4,charging,charging_red}`; the 2x suffix has no underscore (`battery_status_32x.png` = level 3 at 2x)
- `icons/battery/dashboard/`: `vert_battery_1-4`, `vert_charging*`, `controller_model_battery_1-4`, `controller_model_charging*`, and `svr_controller_charge.svg`

States: `off`, `standby`, `searching` (animated `.gif`), `ready`, `ready_low` (low battery), `ready_alert`, `error`. Names with a hash (`*.b4bfb144.png`) look like alternate or older versions of the same icon.

Originals: `C:\Program Files (x86)\Steam\steamapps\common\SteamVR\`, under `resources\icons\`, `drivers\<name>\resources\icons\`, `content\vrmonitor\icons\` and `resources\webinterface\dashboard\images\icons\`. Re-copy from there if SteamVR updates them.

These are Valve's and the driver makers' artwork. Don't redistribute them without checking first.

## Code

`tundra_remote/` is the Discord bot (see README.md). Notes:

- `probe.py` and `poweroff.py` get copied to remote PCs and run as plain scripts, so keep them to the standard library plus `openvr`, with no relative imports.
- Component custom_ids are `tr:<action>:...` and are routed in `TundraRemote.on_interaction`, not in view callbacks, so buttons survive restarts.
- User-facing text is casual and lower-case.
