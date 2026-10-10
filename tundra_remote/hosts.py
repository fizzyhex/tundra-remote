"""Where the probe and power-off run: this PC directly, or another PC over SSH (tailnet)."""
import asyncio
import json
import re
import threading
from pathlib import Path

from . import poweroff, probe
from .config import ROOT, Config, HostConfig
from .store import Store

PROBE_TIMEOUT = 15
POWEROFF_TIMEOUT = 70
UPDATE_TIMEOUT = 30

# what a remote PC needs from tundra_remote/
REMOTE_FILES = ("__init__.py", "probe.py", "poweroff.py")


def _quote(path: str) -> str:
    """Quotes a Windows path only when it needs it. The remote shell is the default, cmd."""
    return f'"{path}"' if " " in path else path


def _sftp_path(path: str) -> str:
    r"""C:\Users\x -> /C:/Users/x, the form Windows OpenSSH's sftp server wants."""
    return "/" + path.replace("\\", "/").lstrip("/")


def _key_id(line: str) -> tuple[str, ...]:
    """(type, base64) of an authorized_keys or .pub line, ignoring options and comments."""
    parts = line.split()
    i = next((i for i, p in enumerate(parts) if p.startswith(("ssh-", "ecdsa-", "sk-"))), None)
    return tuple(parts[i:i + 2]) if i is not None else ()


class HostError(Exception):
    pass


class Host:
    def __init__(self, cfg: HostConfig):
        self.cfg = cfg
        self.name = cfg.name

    async def probe(self) -> dict:
        raise NotImplementedError

    async def poweroff(self, receiver: str) -> None:
        raise NotImplementedError


class LocalHost(Host):
    # openvr init/shutdown isn't safe to overlap
    _lock = threading.Lock()

    def _snapshot(self):
        with self._lock:
            return probe.snapshot()

    async def probe(self) -> dict:
        return await asyncio.wait_for(asyncio.to_thread(self._snapshot), PROBE_TIMEOUT)

    async def poweroff(self, receiver: str) -> None:
        await asyncio.to_thread(poweroff.poweroff, [receiver])


class SshHost(Host):
    def __init__(self, cfg: HostConfig, key: str | None, strict: bool):
        super().__init__(cfg)
        self.key = key
        self.strict = strict

    @property
    def remote_dir(self) -> str:
        return self.cfg.remote_dir or rf"C:\Users\{self.cfg.ssh_user}\AppData\Local\tundra-remote\app"

    def _script(self, name: str) -> str:
        return _quote(f"{self.remote_dir}\\tundra_remote\\{name}")

    def _connect(self):
        import asyncssh
        if self.cfg.host_key:
            # pinned by /host pair
            known_hosts = ([asyncssh.import_public_key(self.cfg.host_key)], [], [])
        else:
            known_hosts = () if self.strict else None
        return asyncssh.connect(
            self.cfg.tailnet_host, username=self.cfg.ssh_user,
            client_keys=[self.key] if self.key and Path(self.key).exists() else None,
            known_hosts=known_hosts,
        )

    async def _run(self, args: str, timeout: float) -> str:
        import asyncssh
        try:
            async with asyncio.timeout(timeout):
                async with self._connect() as conn:
                    command = f"{_quote(self.cfg.python)} {args}"
                    if command.count('"') > 2:
                        # cmd /c strips the outermost pair of quotes when there are more than two
                        command = f'"{command}"'
                    result = await conn.run(command)
        except TimeoutError:
            raise HostError("timed out")
        except (OSError, asyncssh.Error) as e:
            raise HostError(str(e) or type(e).__name__)
        if result.exit_status != 0:
            lines = (result.stderr or result.stdout or "").strip().splitlines()
            raise HostError(lines[-1] if lines else f"exited with {result.exit_status}")
        return result.stdout

    async def _sftp(self, fn, timeout: float = UPDATE_TIMEOUT):
        import asyncssh
        try:
            async with asyncio.timeout(timeout):
                async with self._connect() as conn:
                    async with conn.start_sftp_client() as sftp:
                        return await fn(sftp)
        except TimeoutError:
            raise HostError("timed out")
        except (OSError, asyncssh.Error) as e:
            raise HostError(str(e) or type(e).__name__)

    async def probe(self) -> dict:
        out = await self._run(f"{self._script('probe.py')} --via-task", PROBE_TIMEOUT)
        try:
            return json.loads(out)
        except ValueError:
            raise HostError("got junk back from the probe")

    async def poweroff(self, receiver: str) -> None:
        if not re.fullmatch(r"[0-9A-Za-z-]+", receiver):
            raise HostError(f"bad receiver serial: {receiver!r}")
        await self._run(f"{self._script('poweroff.py')} {receiver}", POWEROFF_TIMEOUT)

    async def update(self) -> None:
        """Copies this repo's probe/poweroff scripts over the remote PC's copies."""
        async def put(sftp):
            dest = _sftp_path(f"{self.remote_dir}\\tundra_remote")
            await sftp.makedirs(dest, exist_ok=True)
            for name in REMOTE_FILES:
                await sftp.put(str(ROOT / "tundra_remote" / name), f"{dest}/{name}")
        await self._sftp(put)

    async def unauthorize(self, public_key: str) -> None:
        """Takes the bot's key back out of the remote PC's authorized_keys. The bot can't get in after this."""
        if not self.cfg.auth_file:
            raise HostError("don't know which authorized_keys file it went into")
        path, key = _sftp_path(self.cfg.auth_file), _key_id(public_key)

        async def strip(sftp):
            async with sftp.open(path, "r") as f:
                text = await f.read()
            kept = [line for line in text.splitlines() if _key_id(line) != key]
            async with sftp.open(path, "w") as f:
                await f.write("".join(line + "\r\n" for line in kept))
        await self._sftp(strip)


def make_hosts(config: Config, store: Store) -> dict[str, Host]:
    """Hosts from config.toml, then the ones added with /host pair (config.toml wins on a name clash)."""
    hosts = {}
    for h in config.hosts:
        hosts[h.name] = LocalHost(h) if h.local else SshHost(h, config.ssh_key, config.ssh_strict)
    for name, entry in store.hosts.items():
        if name not in hosts:
            hosts[name] = SshHost(HostConfig(name=name, **entry), config.ssh_key, config.ssh_strict)
    return hosts


async def probe_all(hosts: dict[str, Host], offline: set[str] = frozenset()) -> dict[str, dict]:
    """Probe every host at once. Unreachable hosts come back as {"steamvr": False, "error": ...}.
    Hosts in offline (tailscale says they're off the tailnet) aren't tried at all."""
    hosts = dict(hosts)  # /host pair can add one mid-probe

    async def one(name: str, h: Host):
        if name in offline:
            return {"steamvr": False, "error": "offline on the tailnet", "offline": True}
        try:
            return await h.probe()
        except (HostError, TimeoutError) as e:
            return {"steamvr": False, "error": str(e) or "timed out", "offline": True}
        except Exception as e:  # the openvr bindings raise all sorts
            return {"steamvr": False, "error": f"{type(e).__name__}: {e}"}
    results = await asyncio.gather(*(one(n, h) for n, h in hosts.items()))
    return dict(zip(hosts, results))
