"""Dedicated Linux/rootless-Docker broker. No business DB, host mounts or shell.

Start separately as an unprivileged service account, with a private config file.
Only the fixed loopback endpoint accepts HMAC-authenticated, replay-fenced jobs.
"""
import argparse
import hmac
import os
from pathlib import Path
import re
import sqlite3
import signal
import stat
import subprocess
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer

from .protocol import MAX_INPUT, MAX_OUTPUT, PATH, HEALTH_PATH, PORT, decode, encode, signature, validate_job, validate_result, package_digest

LABEL = "teruisi.pandas-broker=v1"
NAME = re.compile(r"teruisi-pandas-[a-f0-9]{32}")


def private_file(path):
    path = Path(path)
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_mode & 0o077 or info.st_uid != os.getuid():
        raise ValueError("private_file_required")
    return path


class Broker:
    def __init__(self, config):
        if os.name != "posix" or os.getuid() == 0:
            raise ValueError("unprivileged_linux_required")
        if set(config) != {"docker", "socket", "image", "keyFile", "stateDirectory"}:
            raise ValueError("invalid_config")
        self.docker = str(Path(config["docker"]).resolve(strict=True))
        if not Path(self.docker).is_file() or not re.fullmatch(r"sha256:[a-f0-9]{64}", config["image"]):
            raise ValueError("pinned_image_required")
        socket = Path(config["socket"])
        if not socket.is_absolute() or not stat.S_ISSOCK(socket.lstat().st_mode) or socket.lstat().st_uid != os.getuid():
            raise ValueError("owned_rootless_socket_required")
        self.image = config["image"]
        self.package_digest = package_digest()
        self.key = private_file(config["keyFile"]).read_bytes()
        if not 32 <= len(self.key) <= 128:
            raise ValueError("invalid_key")
        state = Path(config["stateDirectory"])
        info = state.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ValueError("private_state_required")
        import fcntl
        self.lock = open(state / "broker.lock", "a+b")
        fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.ledger = sqlite3.connect(state / "requests.sqlite3")
        self.ledger.execute("CREATE TABLE IF NOT EXISTS requests (nonce TEXT PRIMARY KEY, stamp INTEGER NOT NULL)")
        self.ledger.commit()
        # No inherited database credentials, proxy variables, registry credentials
        # or application secrets enter Docker CLI or any container.
        self.env = {"PATH": "/usr/bin:/bin", "DOCKER_HOST": "unix://" + str(socket), "DOCKER_CONFIG": str(state / "empty-docker-config")}
        Path(self.env["DOCKER_CONFIG"]).mkdir(mode=0o700, exist_ok=True)
        self.poisoned = False
        self.preflight()

    def command(self, args, *, raw=None, timeout=3, maximum=MAX_OUTPUT):
        deadline = getattr(self, "deadline", None)
        if deadline is not None:
            timeout = min(timeout, deadline - time.monotonic())
            if timeout <= 0:
                raise ValueError("execution_timeout")
        proc = subprocess.Popen([self.docker, *args], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, env=self.env, shell=False)
        chunks, overflow = [], threading.Event()
        def read():
            size = 0
            while True:
                chunk = proc.stdout.read(4096)
                if not chunk:
                    break
                size += len(chunk)
                if size > maximum:
                    overflow.set()
                    proc.kill()
                    break
                chunks.append(chunk)
        def write():
            try:
                if raw:
                    proc.stdin.write(raw)
                    proc.stdin.flush()
            except (OSError, ValueError):
                pass
            finally:
                proc.stdin.close()
        reader = threading.Thread(target=read, daemon=True)
        writer = threading.Thread(target=write, daemon=True)
        reader.start()
        writer.start()
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired as error:
            proc.kill()
            proc.wait(timeout=2)
            raise ValueError("execution_timeout") from error
        finally:
            reader.join(timeout=2)
            writer.join(timeout=2)
            proc.stdout.close()
        if reader.is_alive() or writer.is_alive() or overflow.is_set():
            raise ValueError("output_limit")
        if proc.returncode:
            raise ValueError("container_execution_failed")
        return b"".join(chunks)

    def preflight(self):
        info = decode(self.command(["info", "--format", "{{json .}}"], maximum=64000))
        security = " ".join(info.get("SecurityOptions", []))
        if (info.get("OSType") != "linux" or "rootless" not in security or "seccomp" not in security
                or info.get("CgroupVersion") != "2" or info.get("CgroupDriver") != "systemd"
                or any(info.get(k) is not True for k in ["MemoryLimit", "SwapLimit", "PidsLimit", "CpuCfsQuota"])):
            raise ValueError("container_isolation_unavailable")
        image = decode(self.command(["image", "inspect", self.image], maximum=64000))[0]
        if (image.get("Id") != self.image or image.get("Os") != "linux"
                or image["Config"].get("Volumes") or image["Config"].get("Entrypoint") != ["python", "-I", "-B", "/opt/entrypoint.py"]):
            raise ValueError("unapproved_image")
        # A previous broker crash is a hard stop, never silently treated as cleanup.
        if self.command(["ps", "-aq", "--filter", "label=" + LABEL]).strip():
            raise ValueError("unresolved_container_cleanup")

    def reserve(self, nonce, stamp):
        if not re.fullmatch(r"[a-f0-9]{32}", nonce) or not re.fullmatch(r"[0-9]{10}", stamp) or abs(time.time() - int(stamp)) > 60:
            raise ValueError("invalid_request_identity")
        with self.ledger:
            self.ledger.execute("DELETE FROM requests WHERE stamp < ?", (int(time.time()) - 86400,))
            if self.ledger.execute("SELECT COUNT(*) FROM requests").fetchone()[0] >= 10000:
                raise ValueError("request_limit")
            try:
                self.ledger.execute("INSERT INTO requests VALUES (?, ?)", (nonce, int(stamp)))
            except sqlite3.IntegrityError as error:
                raise ValueError("request_replayed") from error

    def cleanup(self, name):
        if not NAME.fullmatch(name):
            raise ValueError("invalid_container_identity")
        # Name generated solely by broker; cleanup is mandatory even on create timeout.
        try:
            self.command(["rm", "--force", name])
        except ValueError:
            pass
        remaining = self.command(["ps", "-aq", "--filter", "name=^/" + name + "$"], maximum=1000)
        if remaining.strip():
            raise ValueError("cleanup_failed")

    def execute(self, job):
        validate_job(job)
        if self.poisoned:
            raise ValueError("cleanup_failed")
        name = "teruisi-pandas-" + uuid.uuid4().hex
        args = ["create", "--pull=never", "--name", name, "--label", LABEL,
                "--network=none", "--read-only", "--user=65532:65532", "--cap-drop=ALL",
                "--security-opt=no-new-privileges:true", "--cpus=1", "--memory=512m", "--memory-swap=512m",
                "--pids-limit=64", "--ulimit=nofile=64:64", "--ulimit=core=0:0", "--ulimit=fsize=16777216:16777216",
                "--restart=no", "--log-driver=none", "--ipc=private", "--cgroupns=private", "--shm-size=16m",
                "--tmpfs=/tmp:rw,noexec,nosuid,nodev,size=16m,mode=1777",
                "--tmpfs=/work:rw,noexec,nosuid,nodev,size=32m,uid=65532,gid=65532,mode=0700",
                "--workdir=/work", "--env=OPENBLAS_NUM_THREADS=1", "--env=OMP_NUM_THREADS=1", "-i", self.image]
        try:
            self.deadline = time.monotonic() + 14
            self.preflight()
            self.command(args)
            # Rootless cgroup delegation can be misconfigured even when advertised.
            config = decode(self.command(["inspect", name], maximum=64000))[0]
            host = config["HostConfig"]
            if (config["Image"] != self.image or config["Config"]["User"] != "65532:65532"
                    or host.get("NetworkMode") != "none" or host.get("ReadonlyRootfs") is not True
                    or host.get("Privileged") or host.get("Binds") or host.get("Devices")
                    or set(host.get("CapDrop") or []) != {"ALL"}
                    or host.get("SecurityOpt") != ["no-new-privileges:true"]
                    or host.get("PidMode") not in {"", "private"} or host.get("IpcMode") != "private"
                    or host.get("CgroupnsMode") != "private" or host.get("LogConfig", {}).get("Type") != "none"
                    or any(v.get("Type") != "tmpfs" for v in config.get("Mounts", []))
                    or host.get("Memory") != 536870912 or host.get("MemorySwap") != 536870912
                    or host.get("NanoCpus") != 1000000000 or host.get("PidsLimit") != 64):
                raise ValueError("container_contract_mismatch")
            raw = self.command(["start", "--attach", "--interactive", name], raw=encode(job), timeout=8)
            state = decode(self.command(["inspect", "--format", "{{json .State}}", name]))
            if state.get("Running") or state.get("OOMKilled") or state.get("ExitCode") != 0:
                raise ValueError("container_execution_failed")
            result = validate_result(decode(raw))
        finally:
            self.deadline = time.monotonic() + 6
            try:
                self.cleanup(name)
            except Exception as error:
                self.poisoned = True
                raise ValueError("cleanup_failed") from error
            finally:
                self.deadline = None
        return {"result": result, "image": self.image, "cleanupVerified": True}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def setup(self):
        self.request.settimeout(3)
        super().setup()

    def log_message(self, *_):
        pass  # Never log request bodies, code, dataset rows or HMAC headers.

    def do_POST(self):
        self.connection.settimeout(3)
        broker = self.server.broker
        nonce, stamp = self.headers.get("X-Nonce", ""), self.headers.get("X-Timestamp", "")
        authenticated = False
        try:
            if (self.path not in {PATH, HEALTH_PATH} or self.client_address[0] != "127.0.0.1"
                    or self.headers.get("Content-Type") != "application/json" or self.headers.get("Transfer-Encoding")
                    or any(len(self.headers.get_all(k, [])) != 1 for k in ["Content-Type", "Content-Length", "X-Nonce", "X-Timestamp", "X-Signature"])):
                raise ValueError("invalid_request")
            size = int(self.headers["Content-Length"])
            if not 1 <= size <= MAX_INPUT:
                raise ValueError("input_limit")
            raw = self.rfile.read(size)
            if len(raw) != size or not hmac.compare_digest(signature(broker.key, stamp, nonce, raw, path=self.path), self.headers["X-Signature"]):
                raise ValueError("invalid_signature")
            authenticated = True
            broker.reserve(nonce, stamp)
            if self.path == HEALTH_PATH:
                if raw != b"{}" or broker.poisoned:
                    raise ValueError("not_ready")
                broker.preflight()
                result = {"status": "ready", "image": broker.image, "cleanupVerified": True, "runnerSha256": broker.package_digest}
            else:
                result = broker.execute(decode(raw))
            status = 200
        except Exception:
            # Failure never becomes an empty successful result; unknown dispatches
            # and duplicate identities are not retried or reused automatically.
            result, status = {"error": "sandbox_failed"}, 503 if authenticated else 403
        raw = encode(result)
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(raw)))
        if authenticated:
            self.send_header("X-Signature", signature(broker.key, stamp, nonce, raw, "response", path=self.path))
        self.end_headers()
        self.wfile.write(raw)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    os.umask(0o077)
    broker = Broker(decode(private_file(args.config).read_bytes()))
    # Single execution slot and bounded socket reads; a second service instance
    # is prevented by the process lock, including after HTTP disconnects.
    server = HTTPServer(("127.0.0.1", PORT), Handler)
    server.broker = broker
    # Finish the current bounded request and its mandatory cleanup before exit.
    signal.signal(signal.SIGTERM, lambda *_: threading.Thread(target=server.shutdown, daemon=True).start())
    server.serve_forever()
    server.server_close()


if __name__ == "__main__":
    main()
