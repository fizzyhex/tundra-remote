r"""Snapshot of the SteamVR devices on this PC, as JSON.

    python -m tundra_remote.probe             # print to stdout
    python -m tundra_remote.probe --file      # also write %LOCALAPPDATA%\tundra-remote\probe.json
    python probe.py --via-task                # run the scheduled task, wait for its file, print it

The --file form is what the TundraRemoteProbe scheduled task runs, so the bot can read the
result over SSH (OpenVR can't be reached from the SSH session itself).
"""
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

OUT_FILE = Path(os.environ.get("LOCALAPPDATA", ".")) / "tundra-remote" / "probe.json"

TASK_NAME = "TundraRemoteProbe"

# openvr.EDeviceActivityLevel values
ACTIVITY = {-1: "unknown", 0: "idle", 1: "active", 2: "active_timeout", 3: "standby", 4: "idle_timeout"}

CLASSES = {1: "headset", 2: "controller", 3: "tracker", 4: "base", 5: "other"}


def steamvr_running() -> bool:
    out = subprocess.run(
        ["tasklist", "/FI", "IMAGENAME eq vrserver.exe", "/NH"],
        capture_output=True, text=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    ).stdout
    return "vrserver.exe" in out


def manifest_version() -> str | None:
    """SteamVR build id from its Steam app manifest, used if OpenVR doesn't give a version."""
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
        acf = lib / "steamapps" / "appmanifest_250820.acf"
        if acf.exists():
            m = re.search(r'"buildid"\s+"(\d+)"', acf.read_text("utf-8", "ignore"))
            if m:
                return f"build {m.group(1)}"
    return None


def _prop(system, fn, index, prop, default=None):
    import openvr
    try:
        return getattr(system, fn)(index, prop)
    except openvr.error_code.TrackedPropertyError:
        return default


def snapshot() -> dict:
    if not steamvr_running():
        return {"steamvr": False, "time": time.time()}

    import openvr
    try:
        openvr.init(openvr.VRApplication_Background)
    except openvr.error_code.InitError as e:
        return {"steamvr": False, "error": str(e), "time": time.time()}
    try:
        system = openvr.VRSystem()
        try:
            version = system.getRuntimeVersion()
        except Exception:
            version = None
        poses = system.getDeviceToAbsoluteTrackingPose(
            openvr.TrackingUniverseStanding, 0, openvr.k_unMaxTrackedDeviceCount)
        devices = []
        for i in range(openvr.k_unMaxTrackedDeviceCount):
            cls = system.getTrackedDeviceClass(i)
            if cls == openvr.TrackedDeviceClass_Invalid:
                continue
            s = lambda p: _prop(system, "getStringTrackedDeviceProperty", i, p)
            b = lambda p: _prop(system, "getBoolTrackedDeviceProperty", i, p)
            has_battery = bool(b(openvr.Prop_DeviceProvidesBatteryStatus_Bool))
            battery = _prop(system, "getFloatTrackedDeviceProperty", i, openvr.Prop_DeviceBatteryPercentage_Float)
            devices.append({
                "class": CLASSES.get(cls, "other"),
                "serial": s(openvr.Prop_SerialNumber_String),
                "model": s(openvr.Prop_ModelNumber_String),
                "manufacturer": s(openvr.Prop_ManufacturerName_String),
                "dongle": s(openvr.Prop_ConnectedWirelessDongle_String) or None,
                "connected": bool(system.isTrackedDeviceConnected(i)),
                "activity": ACTIVITY.get(system.getTrackedDeviceActivityLevel(i), "unknown"),
                "tracking": bool(poses[i].bPoseIsValid),
                "battery": round(battery * 100) if has_battery and battery is not None else None,
                "charging": bool(b(openvr.Prop_DeviceIsCharging_Bool)) if has_battery else False,
            })
        return {"steamvr": True, "version": version or manifest_version(), "devices": devices, "time": time.time()}
    finally:
        openvr.shutdown()


def via_task(timeout: float = 10) -> str:
    """Runs the TundraRemoteProbe scheduled task (which runs in the logged-on user's desktop
    session, where OpenVR is reachable) and returns the JSON it writes."""
    start = time.time()
    r = subprocess.run(["schtasks", "/run", "/tn", TASK_NAME], capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f"couldn't run the {TASK_NAME} task: {(r.stderr or r.stdout).strip()}")
    while time.time() - start < timeout:
        try:
            if OUT_FILE.stat().st_mtime >= start - 1:
                return OUT_FILE.read_text("utf-8")
        except OSError:
            pass
        time.sleep(0.2)
    # the task only runs while someone is logged on
    return json.dumps({"steamvr": False, "error": "probe task didn't answer (nobody logged in?)", "time": time.time()})


def main():
    if "--via-task" in sys.argv:
        print(via_task())
        return
    data = snapshot()
    text = json.dumps(data, indent=2)
    if "--file" in sys.argv:
        OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = OUT_FILE.with_suffix(".tmp")
        tmp.write_text(text, "utf-8")
        tmp.replace(OUT_FILE)
    print(text)


if __name__ == "__main__":
    main()
