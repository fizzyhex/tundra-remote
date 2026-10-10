import asyncio
import logging
import re

import discord
from discord import app_commands

from . import tailnet, views
from .config import Config, HostConfig
from .emojis import Emojis
from .hosts import Host, HostError, SshHost, make_hosts, probe_all
from .pairing import Pairing, ensure_key
from .store import Store

log = logging.getLogger(__name__)

NOPE = "you can't use this"
FRIEND_NOPE = "friends can look, not touch :P"


class TundraRemote(discord.Client):
    def __init__(self, config: Config):
        super().__init__(intents=discord.Intents.none())
        self.config = config
        self.store = Store()
        self.emojis_ = Emojis()
        self.hosts: dict[str, Host] = make_hosts(config, self.store)
        self.pairing = Pairing(config.pair_port, config.ssh_key)
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
        # here rather than on_ready, which fires again after every reconnect
        try:
            await self.emojis_.sync(self)
        except discord.HTTPException as e:
            log.warning("emoji sync failed, using unicode fallbacks: %s", e)

    async def on_ready(self):
        log.info("logged in as %s", self.user)

    # probing / rendering

    async def offline_hosts(self) -> set[str]:
        """SSH hosts that tailscale says are off the tailnet, so /status doesn't wait on them."""
        try:
            _, peers = await tailnet.status()
        except tailnet.TailnetError:
            return set()
        offline = set()
        for name, host in self.hosts.items():
            if isinstance(host, SshHost):
                peer = next((p for p in peers if p.id == host.cfg.node or p.matches(host.cfg.tailnet_host or "")), None)
                if peer and not peer.online:
                    offline.add(name)
        return offline

    async def probe(self) -> dict[str, dict]:
        results = await probe_all(self.hosts, await self.offline_hosts())
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
                return await interaction.response.send_message(f"no pc called `{host}`", ephemeral=True)
            nickname = nickname.strip()[:60] if nickname else None
            self.store.set_host_nick(host, nickname)
            msg = f"`{host}` is now **{nickname}**" if nickname else f"cleared the nickname for `{host}`"
            await interaction.response.send_message(msg, ephemeral=True)

        @host_nick.autocomplete("host")
        async def host_ac(interaction: discord.Interaction, current: str):
            return [app_commands.Choice(name=f"{h} · {self.store.host_nick(h)}"[:100], value=h)
                    for h in self.hosts if current.lower() in f"{h} {self.store.host_nick(h)}".lower()][:25]

        @host_group.command(name="pair", description="set up another pc on your tailnet")
        @app_commands.describe(pc="the pc, from your tailnet", name="what to call it here (defaults to its machine name)")
        async def host_pair(interaction: discord.Interaction, pc: str, name: str | None = None):
            if self.role(interaction.user) != "op":
                return await interaction.response.send_message(NOPE, ephemeral=True)
            await interaction.response.defer(ephemeral=True, thinking=True)
            try:
                _, peers = await tailnet.status()
            except tailnet.TailnetError as e:
                return await interaction.followup.send(f"can't see the tailnet: {e}", ephemeral=True)
            peer = next((p for p in peers if p.id == pc or p.matches(pc)), None)
            if not peer:
                return await interaction.followup.send(f"`{pc}` isn't on your tailnet", ephemeral=True)
            if paired := self.paired_as(peer):
                return await interaction.followup.send(f"**{peer.name}** is already set up as `{paired}`", ephemeral=True)
            name = re.sub(r"[^a-z0-9-]+", "-", (name or peer.name).lower()).strip("-")[:32]
            taken = {p.name for p in self.pairing.pending.values() if p.peer.id != peer.id}
            if not name or name in self.hosts or name in taken:
                return await interaction.followup.send(f"there's already a pc called `{name}`, pick another name", ephemeral=True)

            async def done(cfg: HostConfig):
                self.add_paired(cfg)
                await self.edit_quietly(interaction, f"paired **{peer.name}** as `{name}`. {await self.test_probe(name)}")

            async def expired():
                await self.edit_quietly(interaction, f"the pairing code for **{peer.name}** expired, run /host pair again")

            try:
                command = await self.pairing.begin(peer, name, done, expired)
            except (tailnet.TailnetError, OSError) as e:
                return await interaction.followup.send(f"couldn't start pairing: {e}", ephemeral=True)
            offline = "" if peer.online else "\n-# tailscale says it's offline right now, so turn it on first"
            await interaction.followup.send(
                f"on **{peer.name}**, open powershell as admin (signed in as whoever plays vr there) and run:\n"
                f"```\n{command}\n```\n"
                f"-# it needs python installed. the code only works from {peer.name}, once, for 10 min{offline}",
                ephemeral=True)

        @host_pair.autocomplete("pc")
        async def pair_ac(interaction: discord.Interaction, current: str):
            try:
                _, peers = await tailnet.status()
            except tailnet.TailnetError:
                return []
            peers = [p for p in peers if p.os == "windows" and not self.paired_as(p)
                     and current.lower() in f"{p.name} {p.ip}".lower()]
            peers.sort(key=lambda p: (not p.online, p.name))
            return [app_commands.Choice(name=f"{p.name} · {p.ip}{'' if p.online else ' · offline'}", value=p.ip)
                    for p in peers][:25]

        @host_group.command(name="unpair", description="forget a pc added with /host pair, and take the bot's key off it")
        @app_commands.describe(pc="the pc")
        async def host_unpair(interaction: discord.Interaction, pc: str):
            if self.role(interaction.user) != "op":
                return await interaction.response.send_message(NOPE, ephemeral=True)
            if pc not in self.store.hosts:
                why = "it's in config.toml, take it out there" if pc in self.hosts else "no such pc"
                return await interaction.response.send_message(f"`{pc}` wasn't added with /host pair ({why})", ephemeral=True)
            await interaction.response.defer(ephemeral=True, thinking=True)
            host = self.hosts.get(pc)
            note = ""
            if isinstance(host, SshHost):
                try:
                    await host.unauthorize(ensure_key(self.config.ssh_key))
                    note = " and took the bot's key off it"
                except (HostError, OSError) as e:
                    note = (f", but couldn't take the bot's key off it ({e}). delete the `tundra-remote` line "
                            f"from `{host.cfg.auth_file}` on it by hand")
            self.store.remove_host(pc)
            self.hosts.pop(pc, None)
            self.last.pop(pc, None)
            await interaction.followup.send(f"forgot `{pc}`{note}", ephemeral=True)

        @host_unpair.autocomplete("pc")
        async def unpair_ac(interaction: discord.Interaction, current: str):
            return [app_commands.Choice(name=f"{h} · {self.store.host_nick(h)}"[:100], value=h)
                    for h in self.store.hosts if current.lower() in f"{h} {self.store.host_nick(h)}".lower()][:25]

        @host_group.command(name="update", description="copy the latest probe scripts to the other pcs")
        @app_commands.describe(pc="just this pc (default: all of them)")
        async def host_update(interaction: discord.Interaction, pc: str | None = None):
            if self.role(interaction.user) != "op":
                return await interaction.response.send_message(NOPE, ephemeral=True)
            targets = {h: host for h, host in self.hosts.items() if isinstance(host, SshHost) and pc in (None, h)}
            if not targets:
                return await interaction.response.send_message(
                    f"no other pc called `{pc}`" if pc else "there aren't any other pcs to update", ephemeral=True)
            await interaction.response.defer(ephemeral=True, thinking=True)
            offline = await self.offline_hosts()

            async def one(h: str, host: SshHost) -> str:
                if h in offline:
                    return f"`{h}`: skipped, it's offline"
                try:
                    await host.update()
                    return f"`{h}`: updated"
                except HostError as e:
                    return f"`{h}`: couldn't update: {e}"
            lines = await asyncio.gather(*(one(h, host) for h, host in targets.items()))
            await interaction.followup.send("\n".join(lines), ephemeral=True)

        @host_update.autocomplete("pc")
        async def update_ac(interaction: discord.Interaction, current: str):
            return [app_commands.Choice(name=f"{h} · {self.store.host_nick(h)}"[:100], value=h)
                    for h, host in self.hosts.items()
                    if isinstance(host, SshHost) and current.lower() in f"{h} {self.store.host_nick(h)}".lower()][:25]

        tree.add_command(device)
        tree.add_command(host_group)

    # pairing

    def paired_as(self, peer: tailnet.Peer) -> str | None:
        """Name of the host that's already this tailnet peer, if any."""
        for name, host in self.hosts.items():
            if isinstance(host, SshHost) and (host.cfg.node == peer.id or peer.matches(host.cfg.tailnet_host or "")):
                return name
        return None

    def add_paired(self, cfg: HostConfig):
        self.store.add_host(cfg.name, {k: v for k, v in vars(cfg).items() if k not in ("name", "local") and v is not None})
        self.hosts[cfg.name] = SshHost(cfg, self.config.ssh_key, self.config.ssh_strict)

    async def test_probe(self, name: str) -> str:
        r = (await probe_all({name: self.hosts[name]}))[name]
        self.last[name] = r
        if r.get("steamvr"):
            self.store.remember(name, r.get("devices", []))
            return f"steamvr is running there with {len(r.get('devices', []))} devices"
        if r.get("offline"):
            return f"but the bot couldn't get in: {r.get('error')}"
        return "steamvr isn't running there right now" + (f" ({r['error']})" if r.get("error") else "")

    async def edit_quietly(self, interaction: discord.Interaction, content: str):
        """Edits the /host pair reply. Its token only lasts 15 minutes."""
        try:
            await interaction.edit_original_response(content=content)
        except discord.HTTPException as e:
            log.warning("couldn't update the pairing message: %s", e)

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
