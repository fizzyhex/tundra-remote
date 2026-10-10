"""data.json: nicknames, devices seen before (so off ones still show), paired pcs."""
import json
from pathlib import Path

from .config import ROOT

PATH = ROOT / "data.json"


class Store:
    def __init__(self, path: Path = PATH):
        self.path = path
        try:
            self.data = json.loads(path.read_text("utf-8"))
        except (OSError, ValueError):
            self.data = {}
        for key in ("device_nicks", "host_nicks", "seen", "hosts"):
            self.data.setdefault(key, {})
        self.data.pop("emojis", None)  # older versions saved emoji ids here

    def save(self):
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=2), "utf-8")
        tmp.replace(self.path)

    # nicknames
    def device_nick(self, serial: str) -> str | None:
        return self.data["device_nicks"].get(serial)

    def set_device_nick(self, serial: str, nick: str | None):
        if nick:
            self.data["device_nicks"][serial] = nick
        else:
            self.data["device_nicks"].pop(serial, None)
        self.save()

    def host_nick(self, host: str) -> str:
        return self.data["host_nicks"].get(host, host)

    def set_host_nick(self, host: str, nick: str | None):
        if nick:
            self.data["host_nicks"][host] = nick
        else:
            self.data["host_nicks"].pop(host, None)
        self.save()

    # devices seen per host, keyed by serial
    def seen(self, host: str) -> dict[str, dict]:
        return self.data["seen"].setdefault(host, {})

    def remember(self, host: str, devices: list[dict]):
        seen = self.seen(host)
        changed = False
        for d in devices:
            if not d.get("serial"):
                continue
            entry = {k: d.get(k) for k in ("class", "model", "manufacturer", "dongle")}
            if seen.get(d["serial"]) != entry:
                seen[d["serial"]] = entry
                changed = True
        if changed:
            self.save()

    # pcs added with /host pair, keyed by name. values are HostConfig fields.
    @property
    def hosts(self) -> dict[str, dict]:
        return self.data["hosts"]

    def add_host(self, name: str, entry: dict):
        self.data["hosts"][name] = entry
        self.save()

    def remove_host(self, name: str):
        self.data["hosts"].pop(name, None)
        self.data["seen"].pop(name, None)
        self.save()
