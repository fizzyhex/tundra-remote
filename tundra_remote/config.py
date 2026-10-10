import tomllib
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


@dataclass
class HostConfig:
    name: str                       # id used in data.json and the dropdown
    local: bool = False             # the PC the bot runs on
    tailnet_host: str | None = None # MagicDNS name or tailnet IP
    ssh_user: str | None = None
    python: str = "python"          # python on the remote PC that has tundra_remote installed
    remote_dir: str | None = None   # where tundra_remote lives on the remote PC (cwd for python -m)
    host_key: str | None = None     # pinned ssh host key ("ssh-ed25519 AAAA..."), set by /host pair
    auth_file: str | None = None    # authorized_keys file the bot's key went into, so /host unpair can take it out
    node: str | None = None         # tailscale stable node id, set by /host pair


@dataclass
class Config:
    token: str
    op_ids: set[int]
    friend_ids: set[int] = field(default_factory=set)
    ssh_key: str = str(Path.home() / ".ssh" / "tundra_remote")  # made by /host pair if it's missing
    ssh_strict: bool = True         # check remote host keys against ~/.ssh/known_hosts (paired pcs use their pinned key)
    hosts: list[HostConfig] = field(default_factory=list)
    low_battery: int = 20
    pair_port: int = 8765           # /host pair serves the setup script on this port, on the tailnet ip only


def load(path: Path = ROOT / "config.toml") -> Config:
    raw = tomllib.loads(path.read_text("utf-8"))
    hosts = [HostConfig(**h) for h in raw.get("hosts", [])] or [HostConfig(name="this-pc", local=True)]
    return Config(
        token=raw["token"],
        op_ids={int(i) for i in raw.get("op_ids", [])},
        friend_ids={int(i) for i in raw.get("friend_ids", [])},
        ssh_key=raw.get("ssh_key") or Config.ssh_key,
        ssh_strict=raw.get("ssh_strict", True),
        hosts=hosts,
        low_battery=raw.get("low_battery", 20),
        pair_port=raw.get("pair_port", 8765),
    )
