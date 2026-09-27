"""Bounded operator-only lifecycle of one fixed WSL systemd unit."""
import argparse
import os
from pathlib import Path
import socket
import subprocess
import time


def control(verb):
    if os.name != "nt" or verb not in {"start", "stop"}:
        raise ValueError("invalid_lifecycle")
    wsl = str(Path(os.environ["SystemRoot"]) / "System32/wsl.exe")
    prefix = [wsl, "-d", "TERUISI-Pandas", "-u", "teruisi-pandas", "--exec"]
    env = {k: v for k, v in os.environ.items() if k.upper() in {"SYSTEMROOT", "WINDIR", "TEMP", "TMP", "USERPROFILE", "PATH"}}
    def run(args, timeout):
        return subprocess.run(prefix + args, env=env, capture_output=True, timeout=timeout, check=True).stdout
    run(["/usr/bin/env", "-i", "HOME=/home/teruisi-pandas", "PATH=/usr/bin:/bin",
         "XDG_RUNTIME_DIR=/run/user/1000", "DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus",
         "/usr/bin/systemctl", "--user", verb, "teruisi-pandas.service"], 45)
    if verb == "stop":
        if run(["/usr/bin/docker", "--host=unix:///run/user/1000/docker.sock", "ps", "-aq", "--filter",
                "label=teruisi.pandas-broker=v1"], 5).strip():
            raise ValueError("unresolved_container_cleanup")
    else:
        deadline = time.monotonic() + 10
        while True:
            try:
                with socket.create_connection(("127.0.0.1", 8121), timeout=1):
                    break  # Caller must still perform the signed image/source probe.
            except OSError:
                if time.monotonic() >= deadline:
                    raise ValueError("broker_not_listening")
                time.sleep(.5)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("verb", choices=["start", "stop"])
    args = parser.parse_args()
    try:
        control(args.verb)
        print('{"status":"verified"}')
    except Exception:
        print('{"status":"lifecycle_failed"}')
        raise SystemExit(1)
