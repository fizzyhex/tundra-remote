"""/host pair: a one-time setup URL served on the bot PC's tailnet IP.

The new PC runs `irm <url> | iex` in an admin PowerShell. That fetches scripts/install-remote.ps1
with -PairUrl, which pulls the bot's public key and the probe scripts from here, sets up sshd, and
POSTs back its ssh user, python path and sshd host key. Every request has to come from the
tailnet node the code was made for (checked with tailscale whois), and the server only listens
while a code is pending.
"""
import asyncio
import logging
import re
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable

from aiohttp import web

from . import tailnet
from .config import ROOT, HostConfig
from .hosts import REMOTE_FILES

log = logging.getLogger(__name__)

CODE_TTL = 600
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
INSTALL_SCRIPT = ROOT / "scripts" / "install-remote.ps1"

# these end up on a cmd command line, so keep them boring
SAFE_PATH = re.compile(r'[^"&|<>^%!\r\n]+')
SAFE_USER = re.compile(r'[^"/\\\[\]:;|=,+*?<>@\r\n]+')


def ensure_key(path: str) -> str:
    """The bot's public key, making the key pair first if it doesn't exist."""
    import asyncssh
    priv, pub = Path(path), Path(path + ".pub")
    if not priv.exists():
        priv.parent.mkdir(parents=True, exist_ok=True)
        key = asyncssh.generate_private_key("ssh-ed25519", comment="tundra-remote")
        key.write_private_key(str(priv))
        key.write_public_key(str(pub))
        log.info("made a new ssh key at %s", priv)
    elif not pub.exists():
        asyncssh.read_private_key(str(priv)).write_public_key(str(pub))
    return pub.read_text("utf-8").strip()


@dataclass
class Pending:
    code: str
    peer: tailnet.Peer
    name: str
    done: Callable[[HostConfig], Awaitable[None]]
    expired: Callable[[], Awaitable[None]]
    timer: asyncio.TimerHandle | None = None


class Pairing:
    def __init__(self, port: int, ssh_key: str):
        self.port = port
        self.ssh_key = ssh_key
        self.pending: dict[str, Pending] = {}
        self.runner: web.AppRunner | None = None
        self.base = ""

    async def begin(self, peer: tailnet.Peer, name: str,
                    done: Callable[[HostConfig], Awaitable[None]],
                    expired: Callable[[], Awaitable[None]]) -> str:
        """Starts pairing with peer and returns the command to run on it."""
        ensure_key(self.ssh_key)
        # one code per pc: a new /host pair replaces the old one
        for p in [p for p in self.pending.values() if p.peer.id == peer.id]:
            self._drop(p)
        await self._start()
        code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(8))
        p = Pending(code, peer, name, done, expired)
        p.timer = asyncio.get_running_loop().call_later(CODE_TTL, lambda: asyncio.create_task(self._expire(p)))
        self.pending[code] = p
        return f"irm {self.base}/pair/{code} | iex"

    def _drop(self, p: Pending):
        if self.pending.get(p.code) is p:
            del self.pending[p.code]
        if p.timer:
            p.timer.cancel()

    async def _expire(self, p: Pending):
        if self.pending.get(p.code) is not p:
            return
        self._drop(p)
        await self._stop_if_idle()
        await p.expired()

    # the server

    async def _start(self):
        if self.runner:
            return
        me, _ = await tailnet.status()
        app = web.Application(client_max_size=64 * 1024)
        app.router.add_get("/pair/{code}", self._wrapper)
        app.router.add_get("/pair/{code}/install-remote.ps1", self._install_script)
        app.router.add_get("/pair/{code}/key", self._key)
        app.router.add_get("/pair/{code}/files/{name}", self._file)
        app.router.add_post("/pair/{code}/done", self._done)
        runner = web.AppRunner(app, access_log=None)
        await runner.setup()
        try:
            await web.TCPSite(runner, me.ip, self.port).start()
        except OSError:
            await runner.cleanup()
            raise
        self.runner, self.base = runner, f"http://{me.ip}:{self.port}"
        log.info("pairing server listening on %s", self.base)

    async def _stop_if_idle(self):
        if self.runner and not self.pending:
            runner, self.runner = self.runner, None
            await runner.cleanup()
            log.info("pairing server stopped")

    async def _check(self, request: web.Request) -> Pending:
        """The pending pairing for this request, if it came from the right tailnet node."""
        p = self.pending.get(request.match_info["code"].upper())
        ip = request.remote or ""
        if not p or ip not in p.peer.ips:
            raise web.HTTPNotFound(text="no such pairing code, or it's for a different pc\n")
        try:
            node = await tailnet.whois(ip)
        except tailnet.TailnetError as e:
            raise web.HTTPServiceUnavailable(text=f"couldn't check who you are: {e}\n")
        if node != p.peer.id:
            log.warning("pairing %s: %s is %s, expected %s", p.code, ip, node, p.peer.id)
            raise web.HTTPForbidden(text="this code is for a different pc\n")
        return p

    @staticmethod
    def _text(text: str) -> web.Response:
        return web.Response(text=text, content_type="text/plain", charset="utf-8")

    async def _wrapper(self, request: web.Request):
        p = await self._check(request)
        url = f"{self.base}/pair/{p.code}"
        # in its own scope so it doesn't leave variables in the user's shell
        return self._text(
            "& {\n"
            f"    $s = Invoke-RestMethod '{url}/install-remote.ps1'\n"
            f"    & ([scriptblock]::Create($s)) -PairUrl '{url}'\n"
            "}\n")

    async def _install_script(self, request: web.Request):
        await self._check(request)
        return self._text(INSTALL_SCRIPT.read_text("utf-8-sig"))

    async def _key(self, request: web.Request):
        await self._check(request)
        return self._text(ensure_key(self.ssh_key) + "\n")

    async def _file(self, request: web.Request):
        await self._check(request)
        name = request.match_info["name"]
        if name not in REMOTE_FILES:
            raise web.HTTPNotFound()
        return self._text((ROOT / "tundra_remote" / name).read_text("utf-8"))

    async def _done(self, request: web.Request):
        import asyncssh
        p = await self._check(request)
        try:
            body = await request.json()
            user, python, remote_dir, host_key, auth_file = (
                str(body[k]).strip() for k in ("ssh_user", "python", "remote_dir", "host_key", "auth_file"))
            asyncssh.import_public_key(host_key)
        except (ValueError, KeyError, TypeError, asyncssh.KeyImportError) as e:
            raise web.HTTPBadRequest(text=f"bad pairing report: {e}\n")
        if not SAFE_USER.fullmatch(user) or not all(SAFE_PATH.fullmatch(x) for x in (python, remote_dir, auth_file)):
            raise web.HTTPBadRequest(text="bad pairing report: odd characters in a path or user name\n")

        cfg = HostConfig(name=p.name, tailnet_host=p.peer.ip, ssh_user=user, python=python,
                         remote_dir=remote_dir, host_key=host_key, auth_file=auth_file, node=p.peer.id)
        self._drop(p)
        log.info("paired %s (%s) as %s", p.peer.name, p.peer.ip, p.name)

        async def after():
            await self._stop_if_idle()
            await p.done(cfg)
        asyncio.create_task(after())
        return web.json_response({"name": p.name})
