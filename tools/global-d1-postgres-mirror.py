"""Restore an approved archive into a private cluster and exercise all 23 APIs.

Only test credentials/ports are used. Production executables and the approved
archive are read-only inputs; no production controller or browser task runs.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import secrets
import socket
import subprocess
import sys
import time
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import urlopen

import psycopg
from psycopg import sql

ROOT = Path(__file__).resolve().parents[1]
PRODUCTION = Path(r"D:\teruisi-runtime\django-sales")
BIN = PRODUCTION / "postgresql-17.11/bin"
PORT = 55444
HTTP_OFFSET = 10000
DOMAINS = [
    ("sales", "sales", 8001, "reader", "sales_writer"),
    ("finance", "finance", 8011, "finance_reader", "finance_writer"),
    ("netshop", "netshop", 8021, "netshop_reader", "netshop_writer"),
    ("market", "market", 8031, "market_reader", "market_writer"),
    ("products", "product", 8041, "products_reader", "products_writer"),
    ("inventory", "inventory", 8051, "inventory_reader", "inventory_writer"),
    ("workflow", "workflow", 8061, "workflow_reader", "workflow_writer"),
    ("customer_service", "customer_service", 8071, "customer_service_reader", "customer_service_writer"),
    ("bi", "bi", 8081, "bi_reader", None),
    ("erp_reference", "erp_reference", 8091, "erp_reference_reader", "erp_reference_writer"),
    ("access_control", "access_control", 8101, "access_control_reader", "access_control_writer"),
    ("ai", "ai", 8111, "ai_reader", "ai_writer"),
]


def sha_file(target):
    digest = hashlib.sha256()
    with target.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def ordinary_path(target):
    for candidate in [target, *target.parents]:
        if candidate.is_symlink() or candidate.is_junction():
            raise RuntimeError("mirror_reparse_path_rejected")
    if target.resolve() != target.absolute():
        raise RuntimeError("mirror_path_identity_rejected")


def event(stage, **details):
    print(json.dumps({"stage": stage, **details}, ensure_ascii=False), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backup-directory", required=True)
    parser.add_argument("--approved-manifest-sha256", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--acl-snapshot", required=True)
    parser.add_argument("--approved-acl-sha256", required=True)
    parser.add_argument("--resume-private-cluster", action="store_true")
    args = parser.parse_args()
    if ROOT == Path(r"D:\运营管理系统") or not re.fullmatch(r"[0-9a-f]{12}", args.run_id):
        raise RuntimeError("mirror_requires_isolated_worktree_and_exact_id")
    archive_root = Path(args.backup_directory).absolute()
    if archive_root.parent != PRODUCTION / "backups/postgres-daily" or not re.fullmatch(r"daily-\d{8}T\d{6}Z-[0-9a-f]{12}", archive_root.name):
        raise RuntimeError("mirror_archive_outside_approved_backup_root")
    ordinary_path(archive_root)
    acl_path = Path(args.acl_snapshot).absolute()
    if acl_path.parent != ROOT / ".runtime/d1-retirement":
        raise RuntimeError("mirror_acl_snapshot_outside_evidence_root")
    ordinary_path(acl_path)
    if sha_file(acl_path) != args.approved_acl_sha256:
        raise RuntimeError("mirror_acl_snapshot_digest_mismatch")
    acl = json.loads(acl_path.read_text("utf-8"))
    expected_roles = ["teruisi_" + domain + "_" + kind for domain, _, _, _, writer in DOMAINS for kind in (["reader", "writer"] if writer else ["reader"])]
    if acl.get("version") != "teruisi-mirror-role-acl-v1" or acl.get("roles") != expected_roles:
        raise RuntimeError("mirror_acl_snapshot_roles_invalid")
    manifest_path = archive_root / "backup-manifest.json"
    if sha_file(manifest_path) != args.approved_manifest_sha256:
        raise RuntimeError("mirror_manifest_digest_mismatch")
    manifest = json.loads(manifest_path.read_text("utf-8"))
    archive = archive_root / "teruisi-sales.dump"
    if manifest["status"] != "completed" or manifest["dump"]["fileName"] != archive.name or sha_file(archive) != manifest["dump"]["sha256"]:
        raise RuntimeError("mirror_archive_digest_mismatch")
    run_root = ROOT / ".runtime" / ("global-d1-mirror-" + args.run_id)
    if args.resume_private_cluster:
        ordinary_path(run_root)
        ordinary_path(run_root / "data")
        for target in (run_root / "data").rglob("*"):
            if target.is_symlink() or target.is_junction() or target.is_file() and target.stat().st_nlink != 1:
                raise RuntimeError("mirror_resume_linked_data_rejected")
        if any(line.split("#", 1)[0].strip() for line in (run_root / "data/postgresql.auto.conf").read_text().splitlines()):
            raise RuntimeError("mirror_resume_auto_configuration_rejected")
        prior = json.loads((run_root / "result.json").read_text("utf-8"))
        if (prior.get("version") != "teruisi-global-d1-postgres-mirror-v1" or prior.get("runId") != args.run_id
            or prior.get("backupManifestSha256") != args.approved_manifest_sha256 or prior.get("aclSnapshotSha256") != args.approved_acl_sha256
            or prior.get("clusterStopped") is not True or prior.get("postgresPort") != PORT
            or (run_root / "data/postmaster.pid").exists() or (run_root / "data/PG_VERSION").read_text().strip() != "17"):
            raise RuntimeError("mirror_resume_identity_invalid")
        config_text = (run_root / "data/postgresql.conf").read_text()
        active = [re.sub(r"\s*=\s*", "=", line.split("#", 1)[0].strip()) for line in config_text.splitlines() if line.split("#", 1)[0].strip()]
        defaults = ["max_connections=100", "shared_buffers=128MB", "dynamic_shared_memory_type=windows", "max_wal_size=1GB", "min_wal_size=80MB",
                    "log_file_mode=0640", "log_timezone='Asia/Shanghai'", "datestyle='iso, mdy'", "timezone='Asia/Shanghai'", "lc_messages=C",
                    "lc_monetary=C", "lc_numeric=C", "lc_time=C", "default_text_search_config='pg_catalog.english'"]
        if active != defaults + ["listen_addresses='127.0.0.1'", f"port={PORT}", "max_connections=128", "max_wal_size='4GB'"]:
            raise RuntimeError("mirror_resume_configuration_invalid")
    else:
        run_root.mkdir(exist_ok=False)
    ordinary_path(run_root)
    account = os.environ["USERDOMAIN"] + "\\" + os.environ["USERNAME"]
    subprocess.run(["icacls", str(run_root), "/inheritance:r", "/grant:r", account + ":(OI)(CI)F", "*S-1-5-18:(OI)(CI)F", "*S-1-5-32-544:(OI)(CI)F"], check=True, stdout=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)
    for port in [PORT, *[base + HTTP_OFFSET + offset for _, _, base, _, writer in DOMAINS for offset in range(2 if writer else 1)]]:
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", port))
    admin = "d1_mirror_admin"
    password = secrets.token_hex(32)
    password_file = run_root / "init-password.txt"
    password_file.write_text(password, encoding="ascii")
    environment = {k: v for k, v in os.environ.items() if not k.upper().startswith(("PG", "DJANGO", "TERUISI", "PYTHON", "NODE_OPTIONS"))}
    environment.update(PGHOST="127.0.0.1", PGPORT=str(PORT), PGUSER=admin, PGPASSWORD=password, PGDATABASE="postgres", PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1")
    processes = []
    started = False
    result = {"version": "teruisi-global-d1-postgres-mirror-v1", "runId": args.run_id, "status": "failed",
              "backupManifestSha256": args.approved_manifest_sha256, "postgresPort": PORT, "servicePorts": [],
              "aclSnapshotSha256": args.approved_acl_sha256,
              "resumedPrivateCluster": args.resume_private_cluster,
              "sourceFiles": {name: sha_file(ROOT / "tools" / name) for name in ["global-d1-postgres-mirror.py", "global-d1-mirror-service.py", "global-d1-mirror-worker.mjs"]},
              "productionDatabaseTouched": False, "productionServiceStateChanged": False, "externalActionsEnabled": False}

    def native(arguments, *, env=None, timeout=1800, label="native"):
        log = run_root / (label + ".log")
        with log.open("wb") as stream:
            completed = subprocess.run([str(item) for item in arguments], cwd=ROOT, env=env or environment,
                                       stdout=stream, stderr=stream, timeout=timeout, creationflags=subprocess.CREATE_NO_WINDOW)
        if completed.returncode:
            raise RuntimeError("mirror_command_failed:" + label)

    def connection(database="teruisi_sales", user=admin, role_password=password):
        return psycopg.connect(host="127.0.0.1", port=PORT, user=user, password=role_password, dbname=database, connect_timeout=5)

    try:
        if args.resume_private_cluster:
            # Only our stopped, independently identified test cluster can use
            # offline single-user mode to rotate its ephemeral test password.
            # No network listener, pg_hba change or production credential is used.
            reset = subprocess.run([str(BIN / "postgres.exe"), "--single", "-D", str(run_root / "data"), "postgres"],
                input=("ALTER ROLE d1_mirror_admin PASSWORD '" + password + "';\n").encode("ascii"),
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=environment, timeout=30, creationflags=subprocess.CREATE_NO_WINDOW)
            if reset.returncode:
                raise RuntimeError("mirror_private_password_rotation_failed")
        else:
            native([BIN / "initdb.exe", "-D", run_root / "data", "-U", admin, "--auth=scram-sha-256", "--encoding=UTF8", "--locale=C", "--pwfile", password_file], label="initdb")
        password_file.unlink()
        if not args.resume_private_cluster:
            with (run_root / "data/postgresql.conf").open("a", encoding="utf-8") as stream:
                stream.write(f"\nlisten_addresses='127.0.0.1'\nport={PORT}\nmax_connections=128\nmax_wal_size='4GB'\n")
        native([BIN / "pg_ctl.exe", "-D", run_root / "data", "-l", run_root / "postgres.log", "-w", "-t", "30", "start"], timeout=60, label="pg-start")
        started = True
        role_passwords = {}
        with connection("postgres") as owner:
            owner.autocommit = True
            if not args.resume_private_cluster:
                owner.execute("CREATE ROLE teruisi_sales_owner NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT")
            for domain, _, _, _, writer in DOMAINS:
                for kind in ("reader", "writer") if writer else ("reader",):
                    role = "teruisi_" + domain + "_" + kind
                    role_passwords[role] = secrets.token_hex(32)
                    owner.execute(sql.SQL(("ALTER" if args.resume_private_cluster else "CREATE") + " ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS PASSWORD {}").format(sql.Identifier(role), sql.Literal(role_passwords[role])))
                    owner.execute(sql.SQL("ALTER ROLE {} SET default_transaction_read_only = {}").format(sql.Identifier(role), sql.Literal("on" if kind == "reader" else "off")))
            if not args.resume_private_cluster:
                owner.execute("CREATE DATABASE teruisi_sales OWNER teruisi_sales_owner")
        with connection() as owner:
            owner.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
            owner.execute("REVOKE CREATE,TEMPORARY ON DATABASE teruisi_sales FROM PUBLIC")
        if not args.resume_private_cluster:
            event("mirror_restore_started", archiveBytes=archive.stat().st_size, runRoot=str(run_root))
            native([BIN / "pg_restore.exe", "--single-transaction", "--no-owner", "--role=teruisi_sales_owner", "--dbname=teruisi_sales", archive], label="restore")
        # Daily dumps exclude ACLs. Replay the separately approved read-only
        # catalog snapshot, with no production connection or password fallback.
        with connection() as owner:
            for relkind, relation, role, privilege in acl["relations"]:
                if role not in expected_roles or privilege not in {"SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER", "USAGE", "MAINTAIN"} or relkind not in {"r", "v", "m", "S", "f", "p"}:
                    raise RuntimeError("mirror_relation_grant_invalid")
                owner.execute(sql.SQL("GRANT {} ON {} {}.{} TO {}").format(sql.SQL(privilege), sql.SQL("SEQUENCE" if relkind == "S" else "TABLE"), sql.Identifier("public"), sql.Identifier(relation), sql.Identifier(role)))
            for relation, column, role, privilege in acl["columns"]:
                if role not in expected_roles or privilege not in {"SELECT", "INSERT", "UPDATE", "REFERENCES"}:
                    raise RuntimeError("mirror_column_grant_invalid")
                owner.execute(sql.SQL("GRANT {} ({}) ON TABLE {}.{} TO {}").format(sql.SQL(privilege), sql.Identifier(column), sql.Identifier("public"), sql.Identifier(relation), sql.Identifier(role)))
            for role, privilege in acl["schemas"]:
                if role not in expected_roles or privilege != "USAGE":
                    raise RuntimeError("mirror_schema_grant_invalid")
                owner.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(sql.Identifier(role)))
            for role, privilege in acl["database"]:
                if role not in expected_roles or not (privilege == "CONNECT" or role == "teruisi_sales_writer" and privilege == "TEMPORARY"):
                    raise RuntimeError("mirror_database_grant_invalid")
                owner.execute(sql.SQL("GRANT {} ON DATABASE teruisi_sales TO {}").format(sql.SQL(privilege), sql.Identifier(role)))
        event("mirror_exact_acl_replayed", relationGrants=len(acl["relations"]), columnGrants=len(acl["columns"]))
        spec = importlib.util.spec_from_file_location("mirror_backup_evidence", ROOT / "tools/postgres-consistent-backup.py")
        evidence_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(evidence_module)
        with connection() as owner:
            owner.execute("BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            evidence = evidence_module.collect_evidence(owner, "teruisi_sales", admin)
        if evidence["contentSha256"] != manifest["evidence"]["contentSha256"]:
            raise RuntimeError("mirror_restored_content_mismatch")
        result["restoredContentSha256"] = evidence["contentSha256"]
        event("mirror_restore_verified", contentSha256=evidence["contentSha256"])
        internal_secret = secrets.token_hex(32)
        django_env = {**environment, "PGDATABASE": "teruisi_sales", "DJANGO_SETTINGS_MODULE": "teruisi_backend.settings", "DJANGO_DEBUG": "false",
                      "DJANGO_SECRET_KEY": secrets.token_hex(32), "TERUISI_DJANGO_INTERNAL_SECRET": internal_secret,
                      "TERUISI_DJANGO_SALES_READER_BASE_URL": "http://127.0.0.1:18001",
                      "TERUISI_DJANGO_ENVIRONMENT": "test", "TERUISI_DJANGO_PROCESS_ROLE": "development",
                      "TERUISI_DJANGO_DATABASE_URL": f"postgresql://{admin}:{quote(password)}@127.0.0.1:{PORT}/teruisi_sales"}
        native([sys.executable, "-B", ROOT / "backend/manage.py", "migrate", "--check"], env=django_env, label="migration-check")
        native([sys.executable, "-B", ROOT / "backend/manage.py", "makemigrations", "--check", "--dry-run"], env=django_env, label="migration-dry-run")
        authority_env = {}
        with connection() as owner:
            for domain, prefix, _, _, writer in DOMAINS:
                if not writer:
                    continue
                status, epoch, cutover = owner.execute(sql.SQL("SELECT status,authority_epoch,cutover_id FROM {} WHERE id=1").format(sql.Identifier(prefix + "_write_authority"))).fetchone()
                if status != ("active" if domain == "sales" else "postgres"):
                    raise RuntimeError("mirror_authority_not_postgres:" + domain)
                name = "ERP" if domain == "erp_reference" else domain.upper()
                authority_env["TERUISI_DJANGO_" + name + "_AUTHORITY_EPOCH"] = str(epoch)
                authority_env["TERUISI_DJANGO_" + name + "_CUTOVER_ID"] = cutover
            status, epoch, cutover = owner.execute("SELECT status,authority_epoch,cutover_id FROM workflow_operations_write_authority WHERE id=1").fetchone()
            if status != "postgres":
                raise RuntimeError("mirror_workflow_operations_not_postgres")
            authority_env.update(TERUISI_DJANGO_WORKFLOW_OPERATIONS_AUTHORITY_EPOCH=str(epoch), TERUISI_DJANGO_WORKFLOW_OPERATIONS_CUTOVER_ID=cutover)
        mirror_services = []
        for domain, _, base, reader, writer in DOMAINS:
            for offset, process_role in enumerate([reader, writer] if writer else [reader]):
                kind = "reader" if offset == 0 else "writer"
                role = "teruisi_" + domain + "_" + kind
                port = base + offset + HTTP_OFFSET
                env = {**django_env, **authority_env, "TERUISI_DJANGO_PROCESS_ROLE": process_role,
                       "TERUISI_DJANGO_EXPECT_READ_ONLY": str(offset == 0).lower(),
                       "TERUISI_DJANGO_DATABASE_URL": f"postgresql://{role}:{role_passwords[role]}@127.0.0.1:{PORT}/teruisi_sales",
                       "TERUISI_MIRROR_ROOT": str(run_root), "TERUISI_MIRROR_HTTP_PORT": str(port)}
                mirror_services.append({"domain": domain, "kind": kind, "port": port, "env": env})

        def start_service(item):
            log = (run_root / f"{item['domain']}-{item['kind']}.log").open("ab")
            child = subprocess.Popen([sys.executable, "-B", ROOT / "tools/global-d1-mirror-service.py"], cwd=ROOT / "backend",
                                     env=item["env"], stdout=log, stderr=log, creationflags=subprocess.CREATE_NO_WINDOW)
            item.update(child=child, log=log)
            processes.append(item)

        def ready(item):
            deadline = time.monotonic() + 90
            last_code = "no_response"
            while time.monotonic() < deadline:
                if item["child"].poll() is not None:
                    raise RuntimeError("mirror_service_exited:" + item["domain"] + ":" + item["kind"])
                try:
                    with urlopen(f"http://127.0.0.1:{item['port']}/health/ready", timeout=8) as response:
                        payload = json.load(response)
                        if payload.get("status") == "ready":
                            return item["domain"] + "." + item["kind"]
                except HTTPError as error:
                    last_code = json.load(error).get("code", "not_ready")
                except OSError:
                    pass
                time.sleep(0.4)
            raise RuntimeError("mirror_readiness_failed:" + item["domain"] + ":" + item["kind"] + ":" + str(last_code))

        def stop_service(item):
            child = item["child"]
            if child.poll() is None:
                child.terminate()
                child.wait(timeout=20)
            item["log"].close()

        for item in mirror_services:
            start_service(item)
        with ThreadPoolExecutor(max_workers=4) as executor:
            result["coldStartReady"] = list(executor.map(ready, mirror_services))
        result["servicePorts"] = [item["port"] for item in mirror_services]
        event("mirror_23_services_ready", services=len(result["coldStartReady"]))
        # No source D1 path or D1 file is supplied to any child.
        if any(not target.is_relative_to(run_root / "r2") for target in run_root.rglob("*.sqlite")):
            raise RuntimeError("mirror_unexpected_sqlite_file")
        result["d1FileCount"] = 0
        node_env = {**environment, "TERUISI_D1_MIRROR_INTERNAL_SECRET": internal_secret,
                    "TERUISI_D1_MIRROR_RUN_ROOT": str(run_root), "TERUISI_D1_MIRROR_HTTP_OFFSET": str(HTTP_OFFSET)}
        native(["node", ROOT / "tools/global-d1-mirror-worker.mjs"], env=node_env, timeout=600, label="worker-apis")
        result["worker"] = json.loads((run_root / "worker-result.json").read_text("utf-8"))
        for item in mirror_services:
            stop_service(item)
        processes.clear()
        for item in mirror_services:
            start_service(item)
        with ThreadPoolExecutor(max_workers=4) as executor:
            result["restartReady"] = list(executor.map(ready, mirror_services))
        event("mirror_23_services_restarted", services=len(result["restartReady"]))
        with connection() as owner:
            owner.execute("BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            final_evidence = evidence_module.collect_evidence(owner, "teruisi_sales", admin)
        if final_evidence["contentSha256"] != evidence["contentSha256"]:
            raise RuntimeError("mirror_read_only_api_changed_business_snapshot")
        result["finalContentSha256"] = final_evidence["contentSha256"]
        result["status"] = "completed"
    except Exception as error:
        result["failure"] = str(error) if str(error).startswith("mirror_") else type(error).__name__
        raise
    finally:
        for item in reversed(processes):
            try:
                if item["child"].poll() is None:
                    item["child"].terminate()
                    item["child"].wait(timeout=20)
                item["log"].close()
            except Exception:
                result["cleanupRequiresReview"] = True
        if started:
            ordinary_path(run_root / "data")
            native([BIN / "pg_ctl.exe", "-D", run_root / "data", "-m", "fast", "-w", "-t", "30", "stop"], timeout=60, label="pg-stop")
        password_file.unlink(missing_ok=True)
        result["clusterStopped"] = True
        result_raw = json.dumps(result, indent=2) + "\n"
        with (run_root / ("attempt-" + secrets.token_hex(6) + ".json")).open("x", encoding="utf-8") as stream:
            stream.write(result_raw)
        (run_root / "result.json").write_text(result_raw, encoding="utf-8")
        event("mirror_finished", status=result["status"], evidencePath=str(run_root / "result.json"))


if __name__ == "__main__":
    main()
