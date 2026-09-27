"""Operator-only, create-only install in the dedicated WSL distribution.

Invoke as root with an approved source digest and an independent key on stdin.
Does not start services, install Docker, build images, or read business files.
"""
import argparse
import os
from pathlib import Path
import pwd
import re
import sys

from .protocol import encode, package_digest


def install(image, approved_digest, key):
    if os.name != "posix" or os.getuid() != 0 or len(key) != 32:
        raise ValueError("root_operator_and_independent_key_required")
    digest = package_digest()
    if approved_digest != digest or not re.fullmatch(r"sha256:[a-f0-9]{64}", image):
        raise ValueError("approved_source_and_image_required")
    account = pwd.getpwnam("teruisi-pandas")
    if account.pw_uid != 1000 or account.pw_dir != "/home/teruisi-pandas":
        raise ValueError("dedicated_account_required")
    config = Path(account.pw_dir) / ".config/teruisi-pandas"
    release = Path("/opt/teruisi-pandas/releases") / digest
    unit = Path("/etc/systemd/user/teruisi-pandas.service")
    if config.exists() or release.exists() or unit.exists():
        raise ValueError("existing_install_requires_reviewed_rotation")
    source = Path(__file__).parent
    files = [p for p in sorted(source.glob("*.py")) if not p.name.startswith("test_")]
    if any(p.is_symlink() or not p.is_file() for p in files):
        raise ValueError("ordinary_source_files_required")
    os.umask(0o077)
    (release / "backend/pandas_runner").mkdir(parents=True, mode=0o755)
    for file in files:
        target = release / "backend/pandas_runner" / file.name
        target.write_bytes(file.read_bytes())
        target.chmod(0o444)
    for directory in [release / "backend/pandas_runner", release / "backend", release, release.parent, release.parent.parent]:
        directory.chmod(0o555)
    config.mkdir(mode=0o700)
    (config / "state").mkdir(mode=0o700)
    (config / "key").write_bytes(key)
    (config / "broker.json").write_bytes(encode({"docker": "/usr/bin/docker", "socket": "/run/user/1000/docker.sock",
        "image": image, "keyFile": str(config / "key"), "stateDirectory": str(config / "state")}))
    for file in [config, config / "state", config / "key", config / "broker.json"]:
        os.chown(file, 1000, account.pw_gid)
    unit.write_text(f"""[Unit]
Description=TERUISI pandas controlled container broker
Requires=docker.service
After=docker.service

[Service]
Type=exec
WorkingDirectory={release}/backend
ExecStart=/usr/bin/python3 -B -m pandas_runner.server --config {config}/broker.json
Environment=PYTHONUTF8=1
UMask=0077
NoNewPrivileges=yes
PrivateTmp=yes
KillMode=mixed
TimeoutStopSec=35
Restart=no
StandardOutput=null
StandardError=null
""", encoding="utf-8")
    unit.chmod(0o644)
    print(encode({"status": "installed_stopped", "runnerSha256": digest, "image": image, "releaseDirectory": str(release)}).decode())


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", required=True)
    parser.add_argument("--approved-source-sha256", required=True)
    args = parser.parse_args()
    install(args.image, args.approved_source_sha256, sys.stdin.buffer.read(33))
