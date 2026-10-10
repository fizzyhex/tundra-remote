"""Where the probe and power-off run: this PC directly, or another PC over SSH (tailnet)."""
import asyncio
import json
import re
import threading

from . import poweroff, probe
from .config import Config, HostConfig

PROBE_TIMEOUT = 15
POWEROFF_TIMEOUT = 70


def _quote(path: str) -> str:
    """Quotes a Windows path only when it needs it. The remote shell is the default, cmd."""
    return f'"{path}"' if " " in path else path


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

    def _script(self, name: str) -> str:
        base = self.cfg.remote_dir or rf"C:\Users\{self.cfg.ssh_user}\AppData\Local\tundra-remote\app"
        return _quote(f"{base}\\tundra_remote\\{name}")

    async def _run(self, args: str, timeout: float) -> str:
        import asyncssh
        try:
            async with asyncio.timeout(timeout):
                async with asyncssh.connect(
                    self.cfg.tailnet_host, username=self.cfg.ssh_user,
                    client_keys=[self.key] if self.key else None,
                    known_hosts=() if self.strict else None,
                ) as conn:
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


def make_hosts(config: Config) -> dict[str, Host]:
    hosts = {}
    for h in config.hosts:
        hosts[h.name] = LocalHost(h) if h.local else SshHost(h, config.ssh_key, config.ssh_strict)
    return hosts


async def probe_all(hosts: dict[str, Host]) -> dict[str, dict]:
    """Probe every host at once. Unreachable hosts come back as {"steamvr": False, "error": ...}."""
    async def one(h: Host):
        try:
            return await h.probe()
        except (HostError, TimeoutError) as e:
            return {"steamvr": False, "error": str(e) or "timed out", "offline": True}
        except Exception as e:  # the openvr bindings raise all sorts
            return {"steamvr": False, "error": f"{type(e).__name__}: {e}"}
    results = await asyncio.gather(*(one(h) for h in hosts.values()))
    return dict(zip(hosts, results))
