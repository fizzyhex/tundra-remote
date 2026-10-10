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


@dataclass
class Config:
    token: str
    op_ids: set[int]
    friend_ids: set[int] = field(default_factory=set)
    ssh_key: str | None = None
    ssh_strict: bool = True         # check remote host keys against ~/.ssh/known_hosts
    hosts: list[HostConfig] = field(default_factory=list)
    low_battery: int = 20


def load(path: Path = ROOT / "config.toml") -> Config:
    raw = tomllib.loads(path.read_text("utf-8"))
    hosts = [HostConfig(**h) for h in raw.get("hosts", [])] or [HostConfig(name="this-pc", local=True)]
    return Config(
        token=raw["token"],
        op_ids={int(i) for i in raw.get("op_ids", [])},
        friend_ids={int(i) for i in raw.get("friend_ids", [])},
        ssh_key=raw.get("ssh_key"),
        ssh_strict=raw.get("ssh_strict", True),
        hosts=hosts,
        low_battery=raw.get("low_battery", 20),
    )
