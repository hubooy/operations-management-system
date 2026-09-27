"""Test-only 0072 archive restoration into a SECOND fresh PostgreSQL cluster.

Uses synthetic cluster credentials, a synthetic verifier key and the isolated
source created by ai-postgres-rehearsal.py. Never reads a production archive.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import socket
import struct
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
import django
django.setup()
import psycopg
from psycopg import sql
from django.conf import settings
from protected_ai_archive_v2 import (
    CHUNK_BYTES, MAGIC, MAX_PLAINTEXT_BYTES, TAG_BYTES, open_archive,
    seal_archive,
)
import protected_ai_archive_v2_stream as stream_v2
from protected_ai_cross_cluster_generation import (
    LOGIN_ROLE, LOGIN_TABLE, CAP_APPROVAL, CAP_REVOCATION,
    contract, seed_matches,
)


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--run-root", type=Path, required=True)
parser.add_argument("--generation", choices=("0072", "0073", "0074"), default="0072")
parser.add_argument("--archive-layout", choices=("whole-v2", "stream-v1"),
                    default="whole-v2")
options = parser.parse_args()
if options.archive_layout == "stream-v1" and options.generation != "0073":
    parser.error("stream-v1 is limited to isolated 0073 cross-cluster rehearsal")
folder = options.run_root.resolve()
generation = contract(options.generation)
database = settings.DATABASES["default"]
seed_path = folder / generation.seed_file
seed = json.loads(seed_path.read_text(encoding="utf-8")) if seed_path.is_file() else {}
if (ROOT.resolve() == Path(r"D:\运营管理系统").resolve()
        or folder.parent != (ROOT / ".runtime").resolve()
        or settings.DJANGO_ENVIRONMENT != "test"
        or database["HOST"] != "127.0.0.1"
        or database["NAME"] != "teruisi_ai_rehearsal"
        or database["USER"] != "ai_rehearsal_admin"
        or str(database["PORT"]) != os.getenv("TERUISI_AI_REHEARSAL_PORT")
        or not 55440 <= int(database["PORT"]) <= 55999
        or not seed_matches(seed, generation)):
    raise RuntimeError("cross-cluster restore requires exact isolated "
        + generation.name + " seed")

BIN = Path(r"D:\teruisi-runtime\django-sales\postgresql-17.11\bin")
PROTECTED_ROLES = set(generation.roles)
MIGRATIONS = generation.migrations
modules = [importlib.import_module("ai_assistant.migrations." + name)
           for name in MIGRATIONS]
key_table = modules[0].KEY_TABLE


def run(command: list[object], *, env: dict[str, str], timeout: int = 300) -> None:
    log = folder / ("cross-cluster-command-" + secrets.token_hex(4) + ".log")
    with log.open("wb") as stream:
        completed = subprocess.run([str(part) for part in command], cwd=ROOT,
            env=env, stdout=stream, stderr=subprocess.STDOUT, timeout=timeout,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    if completed.returncode:
        raise RuntimeError("isolated cross-cluster command failed: " + str(log))


def run_sensitive(command: list[object], *, env: dict[str, str],
        input_bytes: bytes | None = None, timeout: int = 600) -> bytes:
    """Keep synthetic custom archive bytes out of command logs and disk files."""
    completed = subprocess.run([str(part) for part in command], cwd=ROOT,
        env=env, input=input_bytes, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, timeout=timeout,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    if completed.returncode:
        raise RuntimeError("isolated protected archive command failed; diagnosticSha256="
            + hashlib.sha256(completed.stderr[:16384]).hexdigest())
    return completed.stdout


class SyntheticArchiveKeys:
    """A single test key in process memory, never a formal custody adapter."""

    def __init__(self, key: bytes, key_id: str):
        self.key = key
        self.key_id = key_id

    def resolve_key(self, key_id: str, purpose: str) -> bytes:
        if key_id != self.key_id or purpose not in {"seal", "open"}:
            raise AssertionError("unexpected synthetic archive key request")
        return self.key


def restore_stream_layout(target_root: Path, source_env: dict[str, str],
        target_env: dict[str, str], archive_context: str) -> dict[str, object]:
    """Test-only pg_dump pipe -> encrypted file -> verified pg_restore pipe."""
    key_id = "isolated_synthetic_archive_key_v2_stream"
    keys = SyntheticArchiveKeys(secrets.token_bytes(32), key_id)
    archive = target_root / "synthetic-source.dump.v2s1.aead"
    command = [BIN / "pg_dump.exe", "--format=custom",
        "teruisi_ai_rehearsal"]
    sealed = stream_v2.seal_process_stdout(command, archive,
        key_id=key_id, context_sha256=archive_context,
        key_provider=keys, env=source_env, timeout_seconds=600)
    with archive.open("rb") as check:
        plaintext_prefix = check.read(5)
    if (not archive.is_file() or any(target_root.glob("*.dump"))
            or plaintext_prefix == b"PGDMP"):
        raise AssertionError("stream archive persisted a plaintext dump")

    rejected = []

    def expect_rejected(label: str, candidate: Path,
            selected_keys: SyntheticArchiveKeys = keys):
        try:
            with stream_v2.open_verified_stream(candidate,
                    expected_key_id=key_id,
                    expected_context_sha256=archive_context,
                    key_provider=selected_keys):
                pass
        except stream_v2.ArchiveInvalid:
            rejected.append(label)
        else:
            raise AssertionError("stream archive accepted " + label)

    expect_rejected("wrong_key", archive,
        SyntheticArchiveKeys(secrets.token_bytes(32), key_id))
    for label in ("truncation", "chunk_tamper"):
        damaged = target_root / (label + ".v2s1.aead")
        shutil.copyfile(archive, damaged)
        try:
            if label == "truncation":
                with damaged.open("r+b") as handle:
                    handle.seek(-1, os.SEEK_END)
                    handle.truncate()
            else:
                with damaged.open("r+b") as handle:
                    handle.seek(len(stream_v2.MAGIC))
                    header_size = struct.unpack(">I", handle.read(4))[0]
                    handle.seek(len(stream_v2.MAGIC) + 4 + header_size + 9)
                    original = handle.read(1)
                    if len(original) != 1:
                        raise AssertionError("stream first chunk is missing")
                    handle.seek(-1, os.SEEK_CUR)
                    handle.write(bytes((original[0] ^ 1,)))
            expect_rejected(label, damaged)
        finally:
            damaged.unlink(missing_ok=True)
    failure_target = target_root / "failed-source.v2s1.aead"
    failed_command = [sys.executable, "-c",
        "import sys;sys.stdout.buffer.write(b'PGDMP'+b'x'*65536);sys.exit(7)"]
    try:
        stream_v2.seal_process_stdout(failed_command, failure_target,
            key_id=key_id, context_sha256=archive_context,
            key_provider=keys, timeout_seconds=15)
    except stream_v2.ArchiveProcessError:
        if (failure_target.exists() or
                any(target_root.glob("failed-source.v2s1.aead.incomplete-*"))):
            raise AssertionError("failed source left a partial stream archive")
    else:
        raise AssertionError("failed source process published a stream archive")

    # Negative cases above run before creating the destination database.
    with stream_v2.open_verified_stream(archive, expected_key_id=key_id,
            expected_context_sha256=archive_context,
            key_provider=keys) as verified:
        if verified.evidence != sealed:
            raise AssertionError("stream seal and first-pass evidence differ")
        run([BIN / "createdb.exe", "teruisi_ai_rehearsal"], env=target_env)
        restored = verified.copy_to_transactional_process(
            [BIN / "pg_restore.exe", "--single-transaction",
             "--exit-on-error", "--dbname", "teruisi_ai_rehearsal"],
            env=target_env, timeout_seconds=600)
        if restored != sealed:
            raise AssertionError("stream second-pass restore evidence differs")
    with archive.open("rb") as source:
        archive_sha = hashlib.file_digest(source, "sha256").hexdigest()
    if any(target_root.glob("*.dump")):
        raise AssertionError("stream restore left a plaintext dump")
    return {"archiveEncryptionVerified": True,
        "archiveLayout": "v2-stream-v1",
        "archiveCipher": "AES-256-GCM-stream-v2-synthetic-only",
        "archiveVersion": 2,
        "archiveChunkBytes": stream_v2.CHUNK_BYTES,
        "archiveChunkCount": sealed.chunk_count,
        "archiveContextSha256": archive_context,
        "streamPlaintextSha256": sealed.plaintext_sha256,
        "streamCiphertextBodySha256": sealed.ciphertext_sha256,
        "encryptedArchiveSha256": archive_sha,
        "plaintextDumpFiles": 0,
        "syntheticKeyPersisted": False,
        "sourceProcessFailureCleaned": True,
        "archiveNegativeCasesRejected": rejected,
        "wrongKeyTamperAndTruncationRejected": True}


def open_db(port: int, password: str) -> psycopg.Connection:
    return psycopg.connect(host="127.0.0.1", port=port,
        dbname="teruisi_ai_rehearsal", user="ai_rehearsal_admin",
        password=password, autocommit=True)


def verify_catalog(db: psycopg.Connection) -> None:
    with db.cursor() as cursor:
        for module in modules:
            module.verify_catalog(cursor)
        cursor.execute("SELECT name FROM django_migrations WHERE "
            "app='ai_assistant' AND name=ANY(%s)", [list(MIGRATIONS)])
        if {row[0] for row in cursor.fetchall()} != set(MIGRATIONS):
            raise AssertionError("protected migration receipts missing")
        cursor.execute("SELECT has_table_privilege(%s,%s,'SELECT')",
            ["teruisi_ai_writer", key_table])
        if cursor.fetchone() != (False,):
            raise AssertionError("ordinary AI writer can read verifier key")
        if generation.name in {"0073", "0074"}:
            cursor.execute("SELECT rolpassword IS NULL FROM pg_catalog.pg_authid "
                "WHERE rolname=%s", [LOGIN_ROLE])
            if cursor.fetchone() != (True,):
                raise AssertionError("0073 protected role gained a password")


def protected_rows(db: psycopg.Connection) -> dict[str, tuple[int, str]]:
    """Hash full synthetic rows in memory; never print private key bytes."""
    tables = [row[0] for row in db.execute(
        "SELECT tablename FROM pg_catalog.pg_tables WHERE schemaname='public' "
        "AND tablename LIKE 'protected_business_%' ORDER BY tablename")]
    if (len(tables) != generation.table_count
            or key_table.removeprefix("public.") not in tables
            or generation.name in {"0073", "0074"} and LOGIN_TABLE not in tables
            or generation.name == "0074" and
              (CAP_APPROVAL not in tables or CAP_REVOCATION not in tables)):
        raise AssertionError("protected table inventory incomplete")
    result = {}
    for table in tables:
        rows = db.execute(sql.SQL("SELECT row_to_json(t) FROM {} t").format(
            sql.Identifier("public", table))).fetchall()
        normalized = sorted(json.dumps(row[0], ensure_ascii=True,
            sort_keys=True, separators=(",", ":")) for row in rows)
        payload = json.dumps(normalized, ensure_ascii=True,
            separators=(",", ":")).encode("ascii")
        result[table] = (len(rows), hashlib.sha256(payload).hexdigest())
    return result


def role_inventory(db: psycopg.Connection) -> list[tuple]:
    rows = db.execute("SELECT rolname,rolcanlogin,rolinherit,rolsuper,"
        "rolcreatedb,rolcreaterole,rolreplication,rolbypassrls "
        "FROM pg_catalog.pg_roles WHERE rolname LIKE 'teruisi_%' "
        "ORDER BY rolname").fetchall()
    if not PROTECTED_ROLES <= {row[0] for row in rows}:
        raise AssertionError("protected role inventory incomplete")
    for name, can_login, inherit, superuser, createdb, createrole, replication, bypass in rows:
        if not re.fullmatch(r"teruisi_[a-z0-9_]{1,64}", name):
            raise AssertionError("unapproved test role name")
        if superuser or createdb or createrole or replication or bypass:
            raise AssertionError("unexpected powerful test role")
        if name in PROTECTED_ROLES and (can_login or inherit):
            raise AssertionError("protected role became login or inheriting")
    members = db.execute("SELECT count(*) FROM pg_catalog.pg_auth_members m "
        "JOIN pg_catalog.pg_roles a ON a.oid=m.roleid "
        "JOIN pg_catalog.pg_roles b ON b.oid=m.member "
        "WHERE a.rolname LIKE 'teruisi_%' OR b.rolname LIKE 'teruisi_%'").fetchone()
    if members != (0,):
        raise AssertionError("test role membership requires separate review")
    return rows


class _RollbackProbe(Exception):
    pass


def assert_catalog_rejects(db: psycopg.Connection, mutation: str) -> None:
    """Prove isolated target drift is rejected, then roll it back."""
    try:
        with db.transaction():
            db.execute(mutation)
            try:
                verify_catalog(db)
            except (AssertionError, RuntimeError, ValueError):
                pass
            else:
                raise AssertionError("protected catalog accepted deliberately mutated target")
            raise _RollbackProbe()
    except _RollbackProbe:
        verify_catalog(db)


source_port = int(database["PORT"])
source_password = str(database["PASSWORD"])
with open_db(source_port, source_password) as source:
    verify_catalog(source)
    if source.execute("SELECT count(*) FROM " + key_table).fetchone() != (0,):
        raise AssertionError("synthetic source key table must start empty")
    source.execute("INSERT INTO " + key_table + " "
        "(key_id,secret,status,created_at) VALUES "
        "('isolated_synthetic_key',%s,'active',now())", [secrets.token_bytes(32)])
    verify_catalog(source)
    before_rows = protected_rows(source)
    if before_rows[key_table.removeprefix("public.")][0] != 1:
        raise AssertionError("synthetic private key fixture missing")
    if generation.name in {"0073", "0074"} and before_rows[LOGIN_TABLE][0] != 0:
        raise AssertionError("0073 source attestation table must remain empty")
    if generation.name == "0074" and (before_rows[CAP_APPROVAL][0] != 0
            or before_rows[CAP_REVOCATION][0] != 0):
        raise AssertionError("0074 source cap tables must remain empty")
    source_roles = role_inventory(source)
    archive_context = hashlib.sha256(json.dumps({
        "migrations": MIGRATIONS,
        "protectedRows": before_rows,
        "roles": source_roles,
    }, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode(
        "ascii")).hexdigest()

target_port = None
for candidate_port in range(55440, 56000):
    if candidate_port == source_port:
        continue
    try:
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", candidate_port))
        target_port = candidate_port
        break
    except OSError:
        pass
if target_port is None:
    raise RuntimeError("no free isolated restore port")

target_root = folder / "protected-cross-cluster"
if target_root.exists() or target_root.parent.resolve() != folder:
    raise RuntimeError("cross-cluster target already exists or escaped run root")
if shutil.disk_usage(folder).free < 4 * 1024**3:
    raise RuntimeError("cross-cluster target requires at least 4 GiB free before initdb")
target_root.mkdir()
data = target_root / "data"
password_file = target_root / ".synthetic-password.tmp"
target_password = secrets.token_hex(32)
password_file.write_text(target_password + "\n", encoding="ascii")
os.chmod(password_file, 0o600)
source_env = {**os.environ, "PGHOST": "127.0.0.1", "PGPORT": str(source_port),
    "PGUSER": "ai_rehearsal_admin", "PGPASSWORD": source_password,
    "PGDATABASE": "teruisi_ai_rehearsal"}
target_env = {**source_env, "PGPORT": str(target_port),
    "PGPASSWORD": target_password}
started = False
try:
    run([BIN / "initdb.exe", "-D", data, "-U", "ai_rehearsal_admin",
        "--auth=scram-sha-256", "--encoding=UTF8", "--locale=C",
        "--pwfile", password_file], env=target_env)
    password_file.unlink()
    with (data / "postgresql.conf").open("a", encoding="utf-8") as config:
        config.write("\nlisten_addresses='127.0.0.1'\n"
            f"port={target_port}\nmax_connections=32\n")
    run([BIN / "pg_ctl.exe", "-D", data, "-l", target_root / "postgres.log",
        "-w", "-t", "30", "start"], env=target_env, timeout=60)
    started = True
    with psycopg.connect(host="127.0.0.1", port=target_port,
            dbname="postgres", user="ai_rehearsal_admin",
            password=target_password, autocommit=True) as admin:
        for name, can_login, inherit, *_ in source_roles:
            admin.execute(sql.SQL("CREATE ROLE {} {} {} NOSUPERUSER NOCREATEDB "
                "NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD NULL").format(
                sql.Identifier(name),
                sql.SQL("LOGIN" if can_login else "NOLOGIN"),
                sql.SQL("INHERIT" if inherit else "NOINHERIT")))
    # The synthetic key exists only in this process. No custom dump is ever
    # written as a plaintext file; the formal backup/restore path stays closed.
    if options.archive_layout == "stream-v1":
        stream_fields = restore_stream_layout(target_root, source_env,
            target_env, archive_context)
        with open_db(target_port, target_password) as target:
            verify_catalog(target)
            after_rows = protected_rows(target)
            target_roles = role_inventory(target)
            assert_catalog_rejects(target, "ALTER TABLE " + key_table +
                " OWNER TO ai_rehearsal_admin")
            assert_catalog_rejects(target, "REVOKE EXECUTE ON FUNCTION " +
                modules[0].VERIFY_SIGNATURE + " FROM " + modules[0].PUBLISHER)
            owner = target.execute("SELECT pg_catalog.pg_get_userbyid(c.relowner) "
                "FROM pg_catalog.pg_class c WHERE c.oid=%s::regclass",
                ["public." + LOGIN_TABLE]).fetchone()
            if owner is None or owner[0] == "teruisi_ai_budget_v11_key_owner":
                raise AssertionError("0073 owner-drift target is not distinct")
            assert_catalog_rejects(target, "ALTER TABLE public." +
                LOGIN_TABLE + " OWNER TO teruisi_ai_budget_v11_key_owner")
            assert_catalog_rejects(target, "REVOKE EXECUTE ON FUNCTION " +
                modules[-1].v2.ATTEST_SIGNATURE + " FROM " + LOGIN_ROLE)
        if before_rows != after_rows or source_roles != target_roles:
            raise AssertionError("stream restored rows or global roles differ")
        stream_result = dict(status="passed",
            scope="isolated synthetic cross-cluster only",
            protectedMigrationsVerified=list(MIGRATIONS),
            protectedRoles=len(PROTECTED_ROLES),
            protectedTables=len(before_rows),
            syntheticVerifierKeyRows=1, ownersAndAclPreserved=True,
            ownerAndAclTamperRejected=True, ordinaryAiKeyReadDenied=True,
            productionWrites=False, formalBackupPathVerified=False,
            privilegedMigrationPathVerified=False,
            newLoginAttestationRows=after_rows[LOGIN_TABLE][0],
            newRoleNoLoginNoPassword=True)
        stream_result.update(stream_fields)
        (target_root / "evidence.json").write_text(json.dumps(stream_result,
            ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(stream_result, ensure_ascii=False))
        sys.exit(0)  # finally stops this exact second fresh cluster.
    plaintext = run_sensitive([BIN / "pg_dump.exe", "--format=custom",
        "teruisi_ai_rehearsal"], env=source_env)
    if not 5 <= len(plaintext) <= MAX_PLAINTEXT_BYTES:
        raise AssertionError("synthetic archive exceeded the bounded test format")
    key_id = "isolated_synthetic_archive_key_v2"
    keys = SyntheticArchiveKeys(secrets.token_bytes(32), key_id)
    archive = target_root / "synthetic-source.dump.v2.aead"
    archive.write_bytes(seal_archive(plaintext, key_id=key_id,
        context_sha256=archive_context, key_provider=keys))
    os.chmod(archive, 0o600)
    del plaintext
    encrypted = archive.read_bytes()
    if encrypted.startswith(b"PGDMP") or any(target_root.glob("*.dump")):
        raise AssertionError("synthetic protected archive persisted in plaintext")
    manifest_size = struct.unpack_from(">I", encrypted, len(MAGIC))[0]
    header_end = len(MAGIC) + 4 + manifest_size
    first_size = struct.unpack_from(">I", encrypted, header_end + 4)[0]
    first_end = header_end + 8 + first_size + TAG_BYTES
    second_size = struct.unpack_from(">I", encrypted, first_end + 4)[0]
    second_end = first_end + 8 + second_size + TAG_BYTES
    if first_size != CHUNK_BYTES or second_size != CHUNK_BYTES:
        raise AssertionError("synthetic protected archive lacks two full v2 chunks")
    tampered = bytearray(encrypted)
    tampered[header_end + 8] ^= 1
    negative_cases = (
        ("wrong_key", encrypted,
         SyntheticArchiveKeys(secrets.token_bytes(32), key_id), archive_context),
        ("truncation", encrypted[:-1], keys, archive_context),
        ("chunk_tamper", bytes(tampered), keys, archive_context),
        ("reordered_chunks", encrypted[:header_end] +
         encrypted[first_end:second_end] + encrypted[header_end:first_end] +
         encrypted[second_end:], keys, archive_context),
        ("duplicated_chunk", encrypted[:header_end] +
         encrypted[header_end:first_end] * 2 + encrypted[second_end:],
         keys, archive_context),
        ("wrong_context", encrypted, keys,
         hashlib.sha256(b"wrong isolated source").hexdigest()),
    )
    rejected_cases = []
    for label, damaged_archive, damaged_keys, expected_context in negative_cases:
        try:
            open_archive(damaged_archive, expected_key_id=key_id,
                expected_context_sha256=expected_context,
                key_provider=damaged_keys)
        except ValueError:
            rejected_cases.append(label)
        else:
            raise AssertionError("tampered synthetic v2 archive authenticated")
    verified = open_archive(encrypted, expected_key_id=key_id,
        expected_context_sha256=archive_context, key_provider=keys)
    if (verified.manifest["version"] != 2
            or verified.manifest["chunkBytes"] != CHUNK_BYTES
            or verified.manifest["chunkCount"] < 2):
        raise AssertionError("synthetic v2 archive manifest is invalid")
    plaintext = verified.plaintext
    toc = run_sensitive([BIN / "pg_restore.exe", "--list"],
        env=target_env, input_bytes=plaintext)
    if (b"protected_business_" not in toc or b"ACL" not in toc
            or generation.name in {"0073", "0074"} and LOGIN_TABLE.encode("ascii") not in toc
            or generation.name == "0074" and (CAP_APPROVAL.encode("ascii") not in toc
                or CAP_REVOCATION.encode("ascii") not in toc)):
        raise AssertionError("synthetic archive TOC lacks protected owner/ACL entries")
    run([BIN / "createdb.exe", "teruisi_ai_rehearsal"], env=target_env)
    # Exact opposite of the formal restore's current owner/ACL suppression.
    run_sensitive([BIN / "pg_restore.exe", "--single-transaction",
        "--exit-on-error", "--dbname", "teruisi_ai_rehearsal"],
        env=target_env, input_bytes=plaintext)
    del plaintext
    with open_db(target_port, target_password) as target:
        verify_catalog(target)
        after_rows = protected_rows(target)
        target_roles = role_inventory(target)
        assert_catalog_rejects(target, "ALTER TABLE " + key_table +
            " OWNER TO ai_rehearsal_admin")
        assert_catalog_rejects(target, "REVOKE EXECUTE ON FUNCTION " +
            modules[0].VERIFY_SIGNATURE + " FROM " + modules[0].PUBLISHER)
        if generation.name in {"0073", "0074"}:
            owner = target.execute("SELECT pg_catalog.pg_get_userbyid(c.relowner) "
                "FROM pg_catalog.pg_class c WHERE c.oid=%s::regclass",
                ["public." + LOGIN_TABLE]).fetchone()
            if owner is None or owner[0] == "teruisi_ai_budget_v11_key_owner":
                raise AssertionError("0073 owner-drift target is not distinct")
            assert_catalog_rejects(target, "ALTER TABLE public." +
                LOGIN_TABLE + " OWNER TO teruisi_ai_budget_v11_key_owner")
            login = next(module for module in modules if
                module.__name__.endswith("0073_business_promotion_budget_v11_login_attestation"))
            assert_catalog_rejects(target, "REVOKE EXECUTE ON FUNCTION " +
                login.v2.ATTEST_SIGNATURE + " FROM " + LOGIN_ROLE)
        if generation.name == "0074":
            assert_catalog_rejects(target, "GRANT INSERT ON public." +
                CAP_APPROVAL + " TO teruisi_ai_writer")
            assert_catalog_rejects(target, "REVOKE EXECUTE ON FUNCTION " +
                modules[-1].cap.APPROVE_SIG + " FROM teruisi_ai_writer")
    if before_rows != after_rows or source_roles != target_roles:
        raise AssertionError("protected rows or global role attributes differ")
    result = {"status": "passed", "scope": "isolated synthetic cross-cluster only",
        "protectedMigrationsVerified": list(MIGRATIONS),
        "protectedRoles": len(PROTECTED_ROLES),
        "protectedTables": len(before_rows),
        "syntheticVerifierKeyRows": 1, "ownersAndAclPreserved": True,
        "ownerAndAclTamperRejected": True,
        "ordinaryAiKeyReadDenied": True, "productionWrites": False,
        "formalBackupPathVerified": False,
        "privilegedMigrationPathVerified": False,
        "archiveEncryptionVerified": True,
        "archiveCipher": "AES-256-GCM-chunked-v2-synthetic-only",
        "archiveVersion": verified.manifest["version"],
        "archiveChunkBytes": verified.manifest["chunkBytes"],
        "archiveChunkCount": verified.manifest["chunkCount"],
        "archiveContextSha256": archive_context,
        "archiveManifestSha256": hashlib.sha256(json.dumps(
            verified.manifest, ensure_ascii=True, sort_keys=True,
            separators=(",", ":")).encode("ascii")).hexdigest(),
        "encryptedArchiveSha256": hashlib.sha256(encrypted).hexdigest(),
        "plaintextDumpFiles": 0,
        "syntheticKeyPersisted": False,
        "archiveNegativeCasesRejected": rejected_cases,
        "wrongKeyTamperAndTruncationRejected": True}
    if generation.name in {"0073", "0074"}:
        result.update({"newLoginAttestationRows": after_rows[LOGIN_TABLE][0],
            "newRoleNoLoginNoPassword": True})
    if generation.name == "0074":
        result.update({"newHumanCapApprovalRows": after_rows[CAP_APPROVAL][0],
            "newHumanCapRevocationRows": after_rows[CAP_REVOCATION][0],
            "modelCallsAllowed": False})
    (target_root / "evidence.json").write_text(json.dumps(result,
        ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
finally:
    if started:
        run([BIN / "pg_ctl.exe", "-D", data, "-m", "fast", "-w",
            "-t", "60", "stop"], env=target_env, timeout=90)
    password_file.unlink(missing_ok=True)
