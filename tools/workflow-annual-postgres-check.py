"""Run workflow/finance contracts using synthetic data in a private PostgreSQL cluster."""
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
BIN = Path(r"D:\teruisi-runtime\django-sales\postgresql-17.11\bin")
PORT = 55461
RUN = ROOT / ".runtime" / ("workflow-annual-pg-" + secrets.token_hex(6))
if not (ROOT / ".git").is_file() or not RUN.resolve().is_relative_to((ROOT / ".runtime").resolve()):
    raise RuntimeError("A separate worktree is required")
RUN.mkdir(parents=True)
with socket.socket() as probe:
    probe.bind(("127.0.0.1", PORT))
password = secrets.token_hex(32)
pwfile = RUN / "password"
pwfile.write_text(password, encoding="ascii")
env = {k: v for k, v in os.environ.items() if not k.startswith(("TERUISI_", "AI_", "PG", "DJANGO_", "PYTHON"))}
env.update(PGHOST="127.0.0.1", PGPORT=str(PORT), PGUSER="annual_fixture", PGPASSWORD=password, PGDATABASE="annual_fixture",
           DJANGO_SETTINGS_MODULE="teruisi_backend.settings", DJANGO_SECRET_KEY=secrets.token_hex(32),
           TERUISI_DJANGO_ENVIRONMENT="test", TERUISI_DJANGO_PROCESS_ROLE="development", DJANGO_DEBUG="1",
           TERUISI_DJANGO_INTERNAL_SECRET=secrets.token_hex(32), PYTHONUTF8="1",
           TERUISI_DJANGO_DATABASE_URL=f"postgresql://annual_fixture:{password}@127.0.0.1:{PORT}/annual_fixture")

def run(args, label, timeout=300):
    logfile = RUN / f"{label}.log"
    with logfile.open("wb") as out:
        result = subprocess.run([str(a) for a in args], cwd=ROOT, env=env, stdout=out, stderr=out,
                                timeout=timeout, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    print(f"{label}: exit={result.returncode}, log={logfile}", flush=True)
    if result.returncode:
        print(logfile.read_text(encoding="utf-8", errors="replace")[-12000:], flush=True)
        raise RuntimeError(f"{label} failed")

started = False
try:
    run([BIN / "initdb.exe", "-D", RUN / "data", "-U", "annual_fixture", "--auth=scram-sha-256", "--encoding=UTF8", "--locale=C", "--pwfile", pwfile], "initdb")
    with (RUN / "data/postgresql.conf").open("a", encoding="ascii") as out:
        out.write(f"\nlisten_addresses='127.0.0.1'\nport={PORT}\nmax_connections=128\n")
    run([BIN / "pg_ctl.exe", "-D", RUN / "data", "-l", RUN / "postgres.log", "-w", "-t", "30", "start"], "start")
    started = True
    run([BIN / "createdb.exe", "annual_fixture"], "createdb")
    run([sys.executable, ROOT / "backend/manage.py", "migrate", "--noinput"], "migrations")
    run([sys.executable, ROOT / "backend/manage.py", "makemigrations", "--check", "--dry-run"], "migration-drift")
    run([sys.executable, ROOT / "backend/manage.py", "test", *(sys.argv[1:] or ["workflow.tests", "finance.tests"]), "--noinput"], "tests", timeout=600)
finally:
    if started:
        run([BIN / "pg_ctl.exe", "-D", RUN / "data", "-m", "fast", "-w", "-t", "30", "stop"], "stop")
    pwfile.unlink(missing_ok=True)
