"""Power off a lighthouse device through its receiver, using SteamVR's lighthouse_console.

    python -m tundra_remote.poweroff <receiver-serial> [<receiver-serial> ...]

Python port of Find-LighthouseConsole / Invoke-Lighthouse from scripts/tundra-off.ps1.
lighthouse_console talks to the USB receivers directly, so this works from an SSH session.
"""
import re
import subprocess
import sys
from pathlib import Path

REL = Path("steamapps/common/SteamVR/tools/lighthouse/bin/win64/lighthouse_console.exe")


def find_lighthouse_console() -> Path:
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as k:
            steam = winreg.QueryValueEx(k, "SteamPath")[0]
    except OSError:
        steam = r"C:\Program Files (x86)\Steam"
    libs = [Path(steam)]
    vdf = Path(steam) / "steamapps" / "libraryfolders.vdf"
    if vdf.exists():
        libs += [Path(p.replace("\\\\", "\\")) for p in re.findall(r'"path"\s+"(.+)"', vdf.read_text("utf-8", "ignore"))]
    for lib in libs:
        if (lib / REL).exists():
            return lib / REL
    raise FileNotFoundError("lighthouse_console.exe not found. is steamvr installed?")


def invoke_lighthouse(commands: list[str], timeout: float = 60) -> str:
    """Runs lighthouse_console with the given commands on stdin and returns its output."""
    # lighthouse_console wants bare LF line endings. The leading blank line soaks up a
    # byte-order mark if one sneaks in; otherwise the first command is garbled and the
    # console spins forever on EOF.
    stdin = "\n" + "\n".join(commands + ["quit"]) + "\n"
    proc = subprocess.run(
        [str(find_lighthouse_console())], input=stdin.encode(), capture_output=True, timeout=timeout,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    return (proc.stdout + proc.stderr).decode("utf-8", "replace")


def poweroff(receivers: list[str]) -> str:
    cmds = []
    for rx in receivers:
        if not re.fullmatch(r"[0-9A-Za-z-]+", rx):
            raise ValueError(f"bad receiver serial: {rx!r}")
        cmds += [f"serial {rx}", "sleep 1500", "poweroff", "sleep 500"]
    return invoke_lighthouse(cmds)


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    print(poweroff(sys.argv[1:]))


if __name__ == "__main__":
    main()
