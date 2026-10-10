"""The tailscale CLI on the bot PC: who's on the tailnet, who's online, and who a connection came from."""
import asyncio
import json
import shutil
from dataclasses import dataclass
from pathlib import Path

DEFAULT_EXE = Path(r"C:\Program Files\Tailscale\tailscale.exe")


class TailnetError(Exception):
    pass


@dataclass
class Peer:
    id: str             # stable node id, the same one whois gives back
    name: str           # machine name from the admin console (first label of the magicdns name)
    dns: str
    os: str
    ips: list[str]
    online: bool

    @property
    def ip(self) -> str:
        return next((ip for ip in self.ips if "." in ip), self.ips[0])

    def matches(self, host: str) -> bool:
        """True if host (a config tailnet_host) is this peer's ip, machine name or magicdns name."""
        host = host.lower().rstrip(".")
        return host in self.ips or host in (self.name.lower(), self.dns.lower())


def _peer(p: dict) -> Peer:
    dns = (p.get("DNSName") or "").rstrip(".")
    return Peer(id=p.get("ID", ""), name=dns.split(".")[0] or p.get("HostName", ""), dns=dns,
                os=p.get("OS", ""), ips=p.get("TailscaleIPs") or [], online=bool(p.get("Online")))


async def _run(*args: str) -> dict:
    exe = shutil.which("tailscale") or (str(DEFAULT_EXE) if DEFAULT_EXE.exists() else None)
    if not exe:
        raise TailnetError("tailscale isn't installed on the bot pc")
    try:
        proc = await asyncio.create_subprocess_exec(exe, *args, stdout=asyncio.subprocess.PIPE,
                                                    stderr=asyncio.subprocess.PIPE)
        out, err = await asyncio.wait_for(proc.communicate(), 10)
    except (OSError, TimeoutError) as e:
        raise TailnetError(f"tailscale {args[0]} failed: {e or 'timed out'}")
    if proc.returncode != 0:
        raise TailnetError(err.decode(errors="replace").strip() or f"tailscale {args[0]} failed")
    try:
        return json.loads(out)
    except ValueError:
        raise TailnetError(f"tailscale {args[0]} gave junk back")


async def status() -> tuple[Peer, list[Peer]]:
    """(this pc, every other node on the tailnet)."""
    data = await _run("status", "--json")
    if not data.get("Self") or data.get("BackendState") not in (None, "Running"):
        raise TailnetError(f"tailscale isn't connected ({data.get('BackendState')})")
    return _peer(data["Self"]), [_peer(p) for p in (data.get("Peer") or {}).values()]


async def whois(ip: str) -> str:
    """The stable node id behind a tailnet ip."""
    data = await _run("whois", "--json", ip)
    return (data.get("Node") or {}).get("StableID", "")
