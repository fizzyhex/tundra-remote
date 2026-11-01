"""The /status message: a Components V2 layout built from probe results.

Nothing here has callbacks. Every component has a "tr:..." custom_id that commands.py
routes in on_interaction, so buttons keep working after the bot restarts.
"""
from collections.abc import Collection
from dataclasses import dataclass

import discord
from discord import ui

from .emojis import Emojis
from .store import Store

ACTIONABLE = {"controller", "tracker"}


@dataclass
class Device:
    serial: str
    kind: str        # emoji kind: a device class, or "tundra"
    cls: str
    label: str
    state: str
    dongle: str | None


def device_label(store: Store, d: dict) -> str:
    nick = store.device_nick(d["serial"])
    if nick:
        return nick
    model = (d.get("model") or d.get("class") or "device").lower()
    return f"{model} {d['serial'][-4:].lower()}"


def join(names: list[str]) -> str:
    """a, b and c"""
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + f" and {names[-1]}"


def can_act(d: Device) -> bool:
    return bool(d.cls in ACTIONABLE and d.state != "off" and d.dongle)


def _components(message: discord.Message | None):
    todo = list(message.components if message else ())
    while todo:
        c = todo.pop()
        todo += getattr(c, "children", None) or ()
        todo += [x for x in (getattr(c, "accessory", None), getattr(c, "component", None)) if x]
        yield c


def selected_in(message: discord.Message | None, host: str) -> set[str]:
    """Serials picked on a /status message: its blue device buttons. Keeping the picks on
    the message itself means they survive a restart, like everything else here."""
    out = set()
    prefix = f"tr:dev:{host}:"
    for c in _components(message):
        cid = getattr(c, "custom_id", None) or ""
        if cid.startswith(prefix) and getattr(c, "style", None) is discord.ButtonStyle.primary:
            out.add(cid[len(prefix):])
    return out


def live_in(message: discord.Message | None) -> bool:
    """Whether a /status message is live: its live button is green."""
    return any((getattr(c, "custom_id", None) or "").startswith("tr:live:")
               and getattr(c, "style", None) is discord.ButtonStyle.success for c in _components(message))


def usable(result: dict) -> bool:
    """SteamVR is up, or it's off but lighthouse_console found receivers to send poweroff through."""
    return bool(result.get("steamvr") or result.get("receivers"))


def devices_for(store: Store, host: str, result: dict, low_battery: int) -> list[Device]:
    """Current devices on a host, plus ones seen before that SteamVR no longer lists (off).
    With SteamVR off, it's the seen devices whose receiver is plugged in, in an unknown state."""
    out = []
    current = {d["serial"]: d for d in result.get("devices", []) if d.get("serial")}
    seen = store.seen(host)
    receivers = None if result.get("steamvr") else set(result.get("receivers") or ())
    for serial in list(current) + [s for s in seen if s not in current]:
        d = current.get(serial) or {**seen[serial], "serial": serial, "connected": False}
        if receivers is not None and d.get("dongle") not in receivers:
            continue
        cls = d.get("class") or "other"
        kind = "tundra" if "tundra" in (d.get("manufacturer") or "").lower() else cls
        battery, charging = d.get("battery"), d.get("charging")

        if receivers is not None:
            state = "unknown"
        elif not d.get("connected"):
            state = "off"
        elif d.get("activity") == "standby":
            state = "standby"
        elif not d.get("tracking"):
            state = "searching"
        elif battery is not None and battery <= low_battery and not charging:
            state = "ready_low"
        else:
            state = "ready"

        label = device_label(store, d)
        if state != "off" and battery is not None:
            label += f" · {battery}%" + (" ⚡" if charging else "")
        out.append(Device(serial, kind, cls, label[:80], state, d.get("dongle")))

    order = {"headset": 0, "controller": 1, "tracker": 2, "other": 3, "base": 4}
    out.sort(key=lambda x: (order.get(x.cls, 3), x.state == "off", x.label))
    return out


def pick_host(results: dict[str, dict], preferred: str | None) -> str | None:
    """preferred if it's usable, else the first pc running SteamVR, else the first with receivers."""
    if preferred in results and usable(results[preferred]):
        return preferred
    running = [h for h, r in results.items() if r.get("steamvr")]
    if running:
        return running[0]
    return next((h for h, r in results.items() if usable(r)), None)


def render(
    *, store: Store, emojis: Emojis, results: dict[str, dict], host: str | None,
    low_battery: int, selected: Collection[str] = (), notice: str | None = None, busy: bool = False,
    live: bool = False,
) -> ui.LayoutView:
    """selected: serials picked to turn off. Picked buttons are blue, which is how
    selected_in() reads the picks back off the message. The live button is green when
    live, for live_in()."""
    view = ui.LayoutView(timeout=60)
    container = ui.Container(accent_colour=discord.Colour(0x3B82C4))
    hosts = [h for h, r in results.items() if usable(r)]

    if host is None:
        header = "## steamvr isn't running"
    else:
        result = results[host]
        updated = int(result.get("time", 0))
        if result.get("steamvr"):
            header = f"## {store.host_nick(host)} ▚ steamvr {result.get('version') or ''}".rstrip()
        else:
            header = f"## {store.host_nick(host)} ▚ (steamvr offline)"
            header += "\n-# can't tell what's on without it, but you can still turn things off"
        if updated:
            header += f"\n-# updated <t:{updated}:R>"
    refresh = ui.Button(
        emoji="\N{ANTICLOCKWISE DOWNWARDS AND UPWARDS OPEN CIRCLE ARROWS}", style=discord.ButtonStyle.secondary,
        custom_id=f"tr:refresh:{host or ''}", disabled=busy,
    )
    container.add_item(ui.Section(ui.TextDisplay(header), accessory=refresh))

    if host is not None:
        devices = devices_for(store, host, results[host], low_battery)
        if not devices:
            container.add_item(ui.TextDisplay("no devices yet"))
        actionable = [d.serial for d in devices if can_act(d)]
        selected = [s for s in actionable if s in selected]
        row = None
        for i, d in enumerate(devices):
            if i % 5 == 0:
                row = ui.ActionRow()
                container.add_item(row)
            row.add_item(ui.Button(
                label=d.label, emoji=emojis.get(d.kind, "off" if d.state == "unknown" else d.state),
                style=discord.ButtonStyle.primary if d.serial in selected else discord.ButtonStyle.secondary,
                custom_id=f"tr:dev:{host}:{d.serial}", disabled=busy or not can_act(d),
            ))

        if selected:
            names = join([f"**{d.label.split(' · ')[0]}**" for d in devices if d.serial in selected])
            container.add_item(ui.Separator())
            container.add_item(ui.TextDisplay(f"turn off {names}?"))
            buttons = [
                ui.Button(label="turn it off" if len(selected) == 1 else f"turn {len(selected)} off",
                          style=discord.ButtonStyle.danger, custom_id=f"tr:yes:{host}", disabled=busy),
                ui.Button(label="nevermind", style=discord.ButtonStyle.secondary,
                          custom_id=f"tr:no:{host}", disabled=busy),
            ]
            if len(selected) < len(actionable):
                buttons.insert(1, ui.Button(label="pick all", style=discord.ButtonStyle.secondary,
                                            custom_id=f"tr:all:{host}", disabled=busy))
            container.add_item(ui.ActionRow(*buttons))

    if len(hosts) > 1:
        container.add_item(ui.ActionRow(ui.Select(
            custom_id="tr:host", placeholder="switch pc", disabled=busy,
            options=[discord.SelectOption(label=store.host_nick(h)[:100], value=h, default=h == host)
                     for h in hosts],
        )))

    footer = []
    for h, r in results.items():
        if h == host and not r.get("steamvr"):
            continue  # the header already says
        if r.get("offline"):
            footer.append(f"couldn't reach {store.host_nick(h)}")
        elif r.get("error") and not r.get("steamvr"):
            footer.append(f"{store.host_nick(h)}: {r['error']}")
        elif not r.get("steamvr"):
            footer.append(f"{store.host_nick(h)}: not running")
    if notice:
        footer[:0] = notice.splitlines()
    if footer:
        container.add_item(ui.TextDisplay("\n".join(f"-# {line}" for line in footer)))

    view.add_item(container)
    view.add_item(ui.ActionRow(ui.Button(
        label="live", style=discord.ButtonStyle.success if live else discord.ButtonStyle.secondary,
        custom_id=f"tr:live:{host or ''}", disabled=busy,
    )))
    return view
