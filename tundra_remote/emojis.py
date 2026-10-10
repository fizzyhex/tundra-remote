"""Status icons from icons/, uploaded as the bot's application emojis.

Names look like tracker_ready, tundra_ready_low, base_off. If an icon can't be uploaded,
lookups fall back to a unicode emoji for its state.
"""
import logging

import discord

from .config import ROOT
from .store import Store

log = logging.getLogger(__name__)

ICONS = ROOT / "icons"
CLASSES = ["headset", "controller", "tracker", "base", "other"]
STATES = ["off", "standby", "searching", "ready", "ready_low", "ready_alert", "error"]
FALLBACK = {
    "off": "\N{MEDIUM BLACK CIRCLE}",
    "standby": "\N{CRESCENT MOON}",
    "searching": "\N{ANTICLOCKWISE DOWNWARDS AND UPWARDS OPEN CIRCLE ARROWS}",
    "ready": "\N{LARGE GREEN CIRCLE}",
    "ready_low": "\N{LARGE YELLOW CIRCLE}",
    "ready_alert": "\N{LARGE ORANGE CIRCLE}",
    "error": "\N{LARGE RED CIRCLE}",
}
# The hashed files are SteamVR's current art; the plain ones are older versions.
VARIANTS = [".b4bfb144", ".6e6c89c9", ""]


def icon_file(kind: str, state: str):
    """kind is one of CLASSES, or "tundra" for Tundra trackers."""
    folder, stem = ("tundra_labs", "tracker") if kind == "tundra" else ("steamvr", kind)
    ext = ".gif" if state == "searching" else ".png"
    for v in VARIANTS:
        p = ICONS / folder / f"{stem}_status_{state}{v}{ext}"
        if p.exists():
            return p
    return None


def emoji_name(kind: str, state: str) -> str:
    return f"{kind}_{state}"


class Emojis:
    def __init__(self, store: Store):
        self.store = store
        self.by_name: dict[str, discord.Emoji] = {}

    async def sync(self, client: discord.Client):
        """Uploads any icon that isn't an application emoji yet."""
        existing = {e.name: e for e in await client.fetch_application_emojis()}
        for kind in CLASSES + ["tundra"]:
            for state in STATES:
                name = emoji_name(kind, state)
                if name in existing:
                    self.by_name[name] = existing[name]
                    continue
                path = icon_file(kind, state)
                if path is None:
                    continue
                try:
                    self.by_name[name] = await client.create_application_emoji(name=name, image=path.read_bytes())
                    log.info("uploaded emoji %s from %s", name, path.relative_to(ROOT))
                except discord.HTTPException as e:
                    log.warning("couldn't upload %s: %s", name, e)
        self.store.emojis.clear()
        self.store.emojis.update({n: str(e.id) for n, e in self.by_name.items()})
        self.store.save()

    def get(self, kind: str, state: str):
        return self.by_name.get(emoji_name(kind, state)) or FALLBACK.get(state)
