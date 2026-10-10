import asyncio
import logging

import discord
from discord import app_commands

from . import views
from .config import Config
from .emojis import Emojis
from .hosts import Host, HostError, make_hosts, probe_all
from .store import Store

log = logging.getLogger(__name__)

NOPE = "you can't use this"
FRIEND_NOPE = "friends can look, not touch"


class TundraRemote(discord.Client):
    def __init__(self, config: Config):
        super().__init__(intents=discord.Intents.none())
        self.config = config
        self.store = Store()
        self.emojis_ = Emojis(self.store)
        self.hosts: dict[str, Host] = make_hosts(config)
        self.last: dict[str, dict] = {}  # last probe results, per host
        self.tree = app_commands.CommandTree(self)
        self._add_commands()

    def role(self, user: discord.abc.User) -> str | None:
        if user.id in self.config.op_ids:
            return "op"
        if user.id in self.config.friend_ids:
            return "friend"
        return None

    async def setup_hook(self):
        await self.tree.sync()

    async def on_ready(self):
        log.info("logged in as %s", self.user)
        try:
            await self.emojis_.sync(self)
        except discord.HTTPException as e:
            log.warning("emoji sync failed, using unicode fallbacks: %s", e)

    # probing / rendering

    async def probe(self) -> dict[str, dict]:
        results = await probe_all(self.hosts)
        for host, r in results.items():
            if r.get("steamvr"):
                self.store.remember(host, r.get("devices", []))
        self.last = results
        return results

    def render(self, results, host, **kw):
        return views.render(store=self.store, emojis=self.emojis_, results=results, host=host,
                            low_battery=self.config.low_battery, **kw)

    def default_host(self, results) -> str | None:
        local = next((h for h, host in self.hosts.items() if host.cfg.local), None)
        return views.pick_host(results, local)

    # commands

    def _add_commands(self):
        tree = self.tree
        installs = app_commands.allowed_installs(guilds=True, users=True)
        contexts = app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)

        @tree.command(name="status", description="show your steamvr devices")
        @installs
        @contexts
        async def status(interaction: discord.Interaction):
            if not self.role(interaction.user):
                return await interaction.response.send_message(NOPE, ephemeral=True)
            await interaction.response.defer(thinking=True)
            results = await self.probe()
            await interaction.followup.send(view=self.render(results, self.default_host(results)))

        device = app_commands.Group(name="device", description="manage devices",
                                    allowed_installs=app_commands.AppInstallationType(guild=True, user=True),
                                    allowed_contexts=app_commands.AppCommandContext(guild=True, dm_channel=True, private_channel=True))
        host_group = app_commands.Group(name="host", description="manage pcs",
                                        allowed_installs=app_commands.AppInstallationType(guild=True, user=True),
                                        allowed_contexts=app_commands.AppCommandContext(guild=True, dm_channel=True, private_channel=True))

        @device.command(name="nick", description="give a device a nickname (leave it empty to clear)")
        @app_commands.describe(device="the device", nickname="what to call it")
        async def device_nick(interaction: discord.Interaction, device: str, nickname: str | None = None):
            if self.role(interaction.user) != "op":
                return await interaction.response.send_message(NOPE, ephemeral=True)
            if not any(device in self.store.seen(h) for h in self.hosts):
                return await interaction.response.send_message(f"haven't seen `{device}` yet, try /status first", ephemeral=True)
            nickname = nickname.strip()[:60] if nickname else None
            self.store.set_device_nick(device, nickname)
            msg = f"`{device}` is now **{nickname}**" if nickname else f"cleared the nickname for `{device}`"
            await interaction.response.send_message(msg, ephemeral=True)

        @device_nick.autocomplete("device")
        async def device_ac(interaction: discord.Interaction, current: str):
            choices = []
            for h in self.hosts:
                for serial, d in self.store.seen(h).items():
                    nick = self.store.device_nick(serial)
                    name = f"{serial} · {nick or d.get('model') or d.get('class')} · {self.store.host_nick(h)}"
                    if current.lower() in name.lower():
                        choices.append(app_commands.Choice(name=name[:100], value=serial))
            return choices[:25]

        @host_group.command(name="nick", description="give a pc a nickname (leave it empty to clear)")
        @app_commands.describe(host="the pc", nickname="what to call it")
        async def host_nick(interaction: discord.Interaction, host: str, nickname: str | None = None):
            if self.role(interaction.user) != "op":
                return await interaction.response.send_message(NOPE, ephemeral=True)
            if host not in self.hosts:
                return await interaction.response.send_message(f"no pc called `{host}` in config.toml", ephemeral=True)
            nickname = nickname.strip()[:60] if nickname else None
            self.store.set_host_nick(host, nickname)
            msg = f"`{host}` is now **{nickname}**" if nickname else f"cleared the nickname for `{host}`"
            await interaction.response.send_message(msg, ephemeral=True)

        @host_nick.autocomplete("host")
        async def host_ac(interaction: discord.Interaction, current: str):
            return [app_commands.Choice(name=f"{h} · {self.store.host_nick(h)}"[:100], value=h)
                    for h in self.hosts if current.lower() in f"{h} {self.store.host_nick(h)}".lower()][:25]

        tree.add_command(device)
        tree.add_command(host_group)

    # buttons and the host dropdown

    async def on_interaction(self, interaction: discord.Interaction):
        if interaction.type is not discord.InteractionType.component:
            return
        custom_id = (interaction.data or {}).get("custom_id", "")
        if not custom_id.startswith("tr:"):
            return
        parts = custom_id.split(":")
        action = parts[1]
        role = self.role(interaction.user)
        if role is None:
            return await interaction.response.send_message(NOPE, ephemeral=True)
        if action in ("dev", "yes", "no") and role != "op":
            return await interaction.response.send_message(FRIEND_NOPE, ephemeral=True)

        try:
            if action == "refresh":
                await interaction.response.defer()
                results = await self.probe()
                host = views.pick_host(results, parts[2] or None)
                await interaction.edit_original_response(view=self.render(results, host))

            elif action == "host":
                await interaction.response.defer()
                results = await self.probe()
                host = views.pick_host(results, interaction.data["values"][0])
                await interaction.edit_original_response(view=self.render(results, host))

            elif action in ("dev", "no"):
                # reuse the last probe so the confirm shows up instantly; after a restart there isn't one
                if self.last:
                    results = self.last
                else:
                    await interaction.response.defer()
                    results = await self.probe()
                host = views.pick_host(results, parts[2])
                view = self.render(results, host, confirm=parts[3] if action == "dev" else None)
                if interaction.response.is_done():
                    await interaction.edit_original_response(view=view)
                else:
                    await interaction.response.edit_message(view=view)

            elif action == "yes":
                await self.turn_off(interaction, parts[2], parts[3])
        except discord.HTTPException:
            log.exception("interaction %s failed", custom_id)

    async def turn_off(self, interaction: discord.Interaction, host: str, serial: str):
        results = self.last
        current = next((d for d in results.get(host, {}).get("devices", []) if d.get("serial") == serial), None)
        known = current or self.store.seen(host).get(serial)
        name = views.device_label(self.store, {**known, "serial": serial}) if known else serial
        await interaction.response.edit_message(
            view=self.render(results, views.pick_host(results, host), confirm=serial, busy=True,
                             notice=f"turning off {name}…"))

        dongle = (known or {}).get("dongle")
        if host not in self.hosts or not dongle:
            notice = f"don't know how to reach {name}"
        else:
            try:
                await self.hosts[host].poweroff(dongle)
                notice = f"turned off {name}"
            except (HostError, OSError, TimeoutError) as e:
                notice = f"couldn't turn off {name}: {e}"

        # SteamVR takes a moment to notice the device dropped
        for _ in range(4):
            await asyncio.sleep(2)
            results = await self.probe()
            d = next((d for d in results.get(host, {}).get("devices", []) if d.get("serial") == serial), None)
            if not d or not d.get("connected"):
                break
        await interaction.edit_original_response(view=self.render(results, views.pick_host(results, host), notice=notice))
