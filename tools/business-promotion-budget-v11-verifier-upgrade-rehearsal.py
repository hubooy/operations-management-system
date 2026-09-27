"""Isolated 0067 -> 0068 protected verifier upgrade/restore; no real key."""
import argparse
import hashlib
import importlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
import django
django.setup()
import psycopg
from psycopg import sql
from django.conf import settings
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

from ai_assistant.table_manifest import AI_TABLES_PRE_MARKET_V2_PAID_REHEARSAL as OLD_TABLES
from business_analysis.contracts import canonical

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--run-root", type=Path, required=True)
folder = parser.parse_args().run_root.resolve()
database = settings.DATABASES["default"]
seed = folder / "business-promotion-budget-v11-attestation-upgrade-evidence.json"
proof = json.loads(seed.read_text(encoding="utf-8")) if seed.is_file() else {}
if (ROOT.resolve() == Path(r"D:\运营管理系统").resolve()
        or settings.DJANGO_ENVIRONMENT != "test"
        or database["HOST"] != "127.0.0.1"
        or str(database["PORT"]) != os.getenv("TERUISI_AI_REHEARSAL_PORT")
        or not 55440 <= int(database["PORT"]) <= 55999
        or database["NAME"] != "teruisi_ai_rehearsal"
        or connection.vendor != "postgresql"
        or folder.parent != (ROOT / ".runtime").resolve()
        or proof.get("upgrade") != "0066->0067"
        or proof.get("oldAiTables") != 85
        or proof.get("newAiTables") != 86
        or proof.get("beforeBackupRestored") is not True
        or proof.get("afterBackupRestored") is not True
        or proof.get("emptyReverseAndReapply") is not True
        or len(OLD_TABLES) != 86):
    raise RuntimeError("0068 requires independently restored isolated 0067 seed")

OLD = [("ai_assistant", "0067_business_promotion_budget_v11_attestation"),
       ("finance", "0004_finance_revision_monotonic"),
       ("netshop", "0003_netshop_source_revision_guard")]
NEW = [("ai_assistant", "0068_business_promotion_budget_v11_verifier_receipt"),
       *OLD[1:]]
with connection.cursor() as cursor:
    cursor.execute("SELECT name FROM django_migrations WHERE app='ai_assistant' "
        "AND name IN ('0067_business_promotion_budget_v11_attestation',"
        "'0068_business_promotion_budget_v11_verifier_receipt') ORDER BY name")
    if [row[0] for row in cursor.fetchall()] != [OLD[0][1]]:
        raise RuntimeError("0068 requires exact 0067 predecessor without 0068")

candidate = importlib.import_module(
    "ai_assistant.migrations.0068_business_promotion_budget_v11_verifier_receipt")
attestation = importlib.import_module(
    "ai_assistant.migrations.0067_business_promotion_budget_v11_attestation")
stage = importlib.import_module(
    "ai_assistant.business_promotion_budget_v11_stage_sql")
BIN = Path(r"D:\teruisi-runtime\django-sales\postgresql-17.11\bin")


def connect(name=None):
    return psycopg.connect(host=database["HOST"], port=database["PORT"],
        dbname=name or database["NAME"], user=database["USER"],
        password=database["PASSWORD"], autocommit=True)


def table_digest(db):
    contents = {}
    for table in sorted(OLD_TABLES):
        rows = db.execute(sql.SQL("SELECT row_to_json(t) FROM {} t").format(
            sql.Identifier(table))).fetchall()
        contents[table] = sorted(json.dumps(row[0], sort_keys=True,
            default=str, ensure_ascii=False) for row in rows)
    return hashlib.sha256(canonical(contents).encode("utf-8")).hexdigest()


def old_files(db):
    rows = db.execute("SELECT id,renderer_version,status,attempt,manifest_json "
        "FROM public.ai_business_file_runs WHERE renderer_version<=11 "
        "ORDER BY id").fetchall()
    result = {}
    for run_id, version, status, attempt, manifest in rows:
        chunks = []
        for table, order in (("ai_business_file_chunks", "format,sequence"),
                ("ai_business_volume_chunks", "volume_index,format,sequence")):
            parts = db.execute("SELECT content,content_digest FROM public." +
                table + " WHERE run_id=%s ORDER BY " + order, [run_id]).fetchall()
            if any(hashlib.sha256(bytes(blob)).hexdigest() != saved
                    for blob, saved in parts):
                raise AssertionError("old file chunk digest mismatch")
            chunks.append((table, len(parts), hashlib.sha256(b"".join(
                bytes(blob) for blob, _ in parts)).hexdigest()))
        result[run_id] = (version, status, attempt, manifest, tuple(chunks))
    if not {1, 2, 3, 4, 5, 6, 7} <= {row[0] for row in result.values()}:
        raise AssertionError("old renderer 1-7 seed missing")
    return result


def functions(db):
    rows = db.execute("SELECT p.oid::regprocedure::text,p.oid,p.prosrc,"
        "p.proacl::text,pg_catalog.pg_get_userbyid(p.proowner),"
        "p.prosecdef,p.proconfig,p.prokind,p.provolatile,p.proparallel,"
        "p.proisstrict,p.proleakproof,p.proretset,p.prorettype::regtype::text,"
        "p.proargtypes::text,l.lanname FROM pg_catalog.pg_proc p "
        "JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace "
        "JOIN pg_catalog.pg_language l ON l.oid=p.prolang "
        "WHERE n.nspname='public' AND left(p.proname,3)='ai_' ORDER BY 1").fetchall()
    result = {signature: details for signature, *details in rows}
    if len(result) != len(rows) or not result:
        raise AssertionError("AI function catalog missing or ambiguous")
    return result


def inventory(db):
    relations = db.execute("SELECT c.oid,c.relname,c.relkind,"
        "pg_catalog.pg_get_userbyid(c.relowner) FROM pg_catalog.pg_class c "
        "JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
        "WHERE n.nspname='public' AND c.relkind IN "
        "('r','p','v','m','f','S','i','I') ORDER BY c.relname").fetchall()
    triggers = db.execute("SELECT t.oid,c.relname,t.tgname,"
        "t.tgfoid::regprocedure::text,pg_catalog.pg_get_triggerdef(t.oid),"
        "t.tgenabled,t.tgdeferrable,t.tginitdeferred "
        "FROM pg_catalog.pg_trigger t JOIN pg_catalog.pg_class c "
        "ON c.oid=t.tgrelid JOIN pg_catalog.pg_namespace n "
        "ON n.oid=c.relnamespace WHERE n.nspname='public' "
        "AND NOT t.tgisinternal ORDER BY c.relname,t.tgname").fetchall()
    return relations, triggers


def privilege_matrix(db):
    result = {}
    for role in ("teruisi_ai_reader", "teruisi_ai_writer"):
        if db.execute("SELECT to_regrole(%s)", [role]).fetchone()[0] is None:
            raise AssertionError("old AI runtime role absent")
        for table in OLD_TABLES:
            result[role, table] = tuple(db.execute(
                "SELECT has_table_privilege(%s,%s,%s)",
                [role, "public." + table, privilege]).fetchone()[0]
                for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE",
                    "TRUNCATE", "REFERENCES")) + tuple(db.execute(
                "SELECT has_any_column_privilege(%s,%s,%s)",
                [role, "public." + table, privilege]).fetchone()[0]
                for privilege in ("SELECT", "INSERT", "UPDATE", "REFERENCES"))
    return result


def table_acl_catalog(db):
    # Independent restore may materialize an implicit owner-default ACL.
    # Compare effective grants and grant options instead of nullable storage.
    tables = db.execute("SELECT c.relname,"
        "CASE WHEN grant_item.grantee=0 THEN 'PUBLIC' "
        "ELSE pg_catalog.pg_get_userbyid(grant_item.grantee) END,"
        "pg_catalog.pg_get_userbyid(grant_item.grantor),"
        "grant_item.privilege_type,grant_item.is_grantable "
        "FROM pg_catalog.pg_class c "
        "JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
        "CROSS JOIN LATERAL pg_catalog.aclexplode(COALESCE(c.relacl,"
        "pg_catalog.acldefault('r',c.relowner))) grant_item "
        "WHERE n.nspname='public' AND c.relname=ANY(%s) ORDER BY c.relname",
        [list(OLD_TABLES)]).fetchall()
    columns = db.execute("SELECT c.relname,a.attname,"
        "CASE WHEN grant_item.grantee=0 THEN 'PUBLIC' "
        "ELSE pg_catalog.pg_get_userbyid(grant_item.grantee) END,"
        "pg_catalog.pg_get_userbyid(grant_item.grantor),"
        "grant_item.privilege_type,grant_item.is_grantable "
        "FROM pg_catalog.pg_attribute a JOIN pg_catalog.pg_class c "
        "ON c.oid=a.attrelid JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
        "CROSS JOIN LATERAL pg_catalog.aclexplode(COALESCE(a.attacl,"
        "pg_catalog.acldefault('c',c.relowner))) grant_item "
        "WHERE n.nspname='public' AND c.relname=ANY(%s) "
        "AND a.attnum>0 AND NOT a.attisdropped "
        "ORDER BY c.relname,a.attnum,grant_item.grantee,"
        "grant_item.privilege_type,grant_item.grantor", [list(OLD_TABLES)]).fetchall()
    if len({row[0] for row in tables}) != 86:
        raise AssertionError("0068 old AI table ACL inventory incomplete")
    return sorted(tables), columns


def constraint_versions(db):
    rows = db.execute("SELECT c.convalidated,pg_catalog.pg_get_constraintdef(c.oid) "
        "FROM pg_catalog.pg_constraint c WHERE c.conrelid="
        "'public.ai_business_file_runs'::regclass "
        "AND c.conname='ai_business_file_bound' AND c.contype='c'").fetchall()
    if len(rows) != 1 or rows[0][0] is not True:
        raise AssertionError("file version CHECK missing or unvalidated")
    match = re.search(r"renderer_version\s*=\s*ANY\s*\(ARRAY\[([0-9,\s]+)\]\)",
        rows[0][1])
    if match is None or rows[0][1].count("renderer_version") != 1 or re.search(
            r"\bOR\b", rows[0][1], re.IGNORECASE):
        raise AssertionError("file version CHECK widened unexpectedly")
    return tuple(int(piece.strip()) for piece in match.group(1).split(","))


def snapshot(db):
    return (table_digest(db), old_files(db), functions(db), inventory(db),
        privilege_matrix(db), table_acl_catalog(db), constraint_versions(db),
        role_catalog(db), role_memberships(db))


def role_catalog(db):
    return db.execute("SELECT rolname,rolcanlogin,rolinherit,rolsuper,"
        "rolcreatedb,rolcreaterole,rolreplication,rolbypassrls "
        "FROM pg_catalog.pg_roles WHERE rolname LIKE 'teruisi_ai_%' "
        "ORDER BY rolname").fetchall()


def role_memberships(db):
    return db.execute("SELECT parent.rolname,member.rolname,"
        "membership.admin_option FROM pg_catalog.pg_auth_members membership "
        "JOIN pg_catalog.pg_roles parent ON parent.oid=membership.roleid "
        "JOIN pg_catalog.pg_roles member ON member.oid=membership.member "
        "WHERE parent.rolname LIKE 'teruisi_ai_%' OR "
        "member.rolname LIKE 'teruisi_ai_%' "
        "ORDER BY parent.rolname,member.rolname").fetchall()


def restored(value):
    return (value[0], value[1],
        {key: details[1:] for key, details in value[2].items()},
        (tuple(tuple(row[1:]) for row in value[3][0]),
         tuple(tuple(row[1:]) for row in value[3][1])),
        value[4], value[5], value[6], value[7], value[8])


def archive_restore(name):
    archive = folder / (name + ".dump")
    env = {**os.environ, "PGHOST": str(database["HOST"]),
        "PGPORT": str(database["PORT"]), "PGUSER": str(database["USER"]),
        "PGPASSWORD": str(database["PASSWORD"])}
    for executable, arguments in (
        ("pg_dump.exe", ["-Fc", "-f", str(archive), database["NAME"]]),
        ("createdb.exe", [name]),
        ("pg_restore.exe", ["--exit-on-error", "-d", name, str(archive)]),
    ):
        done = subprocess.run([str(BIN / executable), *arguments], env=env,
            capture_output=True, timeout=120,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        if done.returncode:
            (folder / (name + "-error.log")).write_bytes(done.stderr)
            raise RuntimeError("isolated 0068 archive/restore failed")


def verify_old(db):
    with db.cursor() as cursor:
        stage.verify_catalog(cursor)
        attestation.verify_catalog(cursor)
    if db.execute("SELECT to_regclass(%s)", [candidate.KEY_TABLE]).fetchone()[0]:
        raise AssertionError("0068 private key table exists before install or after reverse")
    for signature in (candidate.MAC_SIGNATURE, candidate.VERIFY_SIGNATURE,
            "public.ai_budget_v11_key_guard()"):
        if db.execute("SELECT to_regprocedure(%s)", [signature]).fetchone()[0]:
            raise AssertionError("0068 verifier function exists before install")
    run = db.execute("SELECT p.prosrc FROM pg_catalog.pg_proc p WHERE "
        "p.oid=to_regprocedure(%s)",
        ["public.ai_business_files_guard()"] ).fetchone()
    complete = db.execute("SELECT p.prosrc FROM pg_catalog.pg_proc p WHERE "
        "p.oid=to_regprocedure(%s)",
        ["public.ai_business_volume_complete_guard()"] ).fetchone()
    if (run != (stage.RUN_GUARD.split("$$", 2)[1],) or
            complete != (stage.COMPLETE_GUARD.split("$$", 2)[1],) or
            "ai_budget_v11_ready_unpublished" not in run[0] or
            "ai_budget_v11_ready_unpublished" not in complete[0]):
        raise AssertionError("0067 staged-only v11 ready denial changed")


def verify_new(db, before, after, *, compare_oid=True):
    if (after[0:2] != before[0:2] or after[4:7] != before[4:7] or
            after[8] != before[8] or
            before[6] != (1, 2, 3, 4, 5, 6, 7, 9, 10, 11)):
        raise AssertionError("0068 altered old 86 tables, 1-11 files, ACL or CHECK")
    with db.cursor() as cursor:
        candidate.verify_catalog(cursor)
        attestation.verify_catalog(cursor)
    if any((after[2].get(key) if compare_oid else
            after[2].get(key, ())[1:]) !=
           (prior if compare_oid else prior[1:])
           for key, prior in before[2].items()):
        raise AssertionError("0068 changed old AI function OID/body/ACL/owner")
    new_functions = {db.execute("SELECT to_regprocedure(%s)::text",
        [signature]).fetchone()[0] for signature in (
            "public.ai_budget_v11_key_guard()",
            candidate.MAC_SIGNATURE, candidate.VERIFY_SIGNATURE)}
    if None in new_functions or set(after[2]) - set(before[2]) != new_functions:
        raise AssertionError("0068 did not add exactly three verifier functions")
    old_relations, old_triggers = before[3]
    new_relations, new_triggers = after[3]
    old_relation_set = {tuple(row if compare_oid else row[1:])
        for row in old_relations}
    new_relation_set = {tuple(row if compare_oid else row[1:])
        for row in new_relations}
    old_trigger_set = {tuple(row if compare_oid else row[1:])
        for row in old_triggers}
    new_trigger_set = {tuple(row if compare_oid else row[1:])
        for row in new_triggers}
    if not old_relation_set <= new_relation_set or not old_trigger_set <= new_trigger_set:
        raise AssertionError("0068 changed old relations or triggers")
    index_names = {item[0] for item in db.execute(
        "SELECT indexname FROM pg_catalog.pg_indexes WHERE schemaname='public' "
        "AND tablename=%s", [candidate.KEY_TABLE.split(".", 1)[1]]).fetchall()}
    added_relations = {item[1] for item in new_relations if tuple(
        item if compare_oid else item[1:]) not in old_relation_set}
    if added_relations != {candidate.KEY_TABLE.split(".", 1)[1], *index_names}:
        raise AssertionError("0068 added unexpected relation or index")
    added_triggers = [item for item in new_triggers if tuple(
        item if compare_oid else item[1:]) not in old_trigger_set]
    if (len(added_triggers) != 2 or
            {item[2] for item in added_triggers} != {
                "ai_budget_v11_key_guard",
                "ai_budget_v11_key_no_truncate"} or
            any(item[1] != candidate.KEY_TABLE.split(".", 1)[1]
                for item in added_triggers)):
        raise AssertionError("0068 added unexpected trigger")
    new_roles = {(role, *(False,) * 7)
        for role in (candidate.KEY_OWNER, candidate.PUBLISHER)}
    if set(after[7]) != set(before[7]) | new_roles:
        raise AssertionError("0068 changed old roles or activated verifier")
    if db.execute("SELECT count(*) FROM " + candidate.KEY_TABLE).fetchone() != (0,):
        raise AssertionError("0068 migration provisioned a key")
    verify_ready(db)


def verify_ready(db):
    run = db.execute("SELECT p.prosrc FROM pg_catalog.pg_proc p WHERE "
        "p.oid=to_regprocedure('public.ai_business_files_guard()')").fetchone()
    complete = db.execute("SELECT p.prosrc FROM pg_catalog.pg_proc p WHERE "
        "p.oid=to_regprocedure('public.ai_business_volume_complete_guard()')"
        ).fetchone()
    if (run != (stage.RUN_GUARD.split("$$", 2)[1],) or
            complete != (stage.COMPLETE_GUARD.split("$$", 2)[1],) or
            "ai_budget_v11_ready_unpublished" not in run[0] or
            "ai_budget_v11_ready_unpublished" not in complete[0]):
        raise AssertionError("0068 opened v11 ready gate")


with connect() as db:
    verify_old(db)
    before = snapshot(db)
archive_restore("budget_v11_verifier_before")
with connect("budget_v11_verifier_before") as copy:
    verify_old(copy)
    if restored(snapshot(copy)) != restored(before):
        raise AssertionError("0067 pre-upgrade independent restore differs")

MigrationExecutor(connection).migrate(NEW)
with connect() as db:
    after = snapshot(db)
    verify_new(db, before, after)
archive_restore("budget_v11_verifier_after")
with connect("budget_v11_verifier_after") as copy:
    recovered = snapshot(copy)
    verify_new(copy, before, recovered, compare_oid=False)
    if restored(recovered) != restored(after):
        raise AssertionError("0068 post-upgrade independent restore differs")

MigrationExecutor(connection).migrate(OLD)
with connect() as db:
    verify_old(db)
    reversed_state = snapshot(db)
    if reversed_state[:7] != before[:7] or reversed_state[8] != before[8] or not set(before[7]) <= set(
            reversed_state[7]) or set(reversed_state[7]) - set(before[7]) != {
                (role, *(False,) * 7) for role in
                (candidate.KEY_OWNER, candidate.PUBLISHER)}:
        raise AssertionError("0068 empty reverse did not restore exact 0067 state")
MigrationExecutor(connection).migrate(NEW)
with connect() as db:
    replayed = snapshot(db)
    verify_new(db, before, replayed)
    if restored(replayed) != restored(after):
        raise AssertionError("0068 reapply differed from first installation")

result = {"upgrade": "0067->0068", "oldAiTables": 86, "newAiTables": 86,
    "oldRowsDigestPreserved": before[0],
    "seededRendererVersions": sorted({row[0] for row in before[1].values()}),
    "unseededRendererVersions": sorted(set(range(1, 12)) -
        {row[0] for row in before[1].values()}),
    "oldRendererRowsAndChunksPreserved": True,
    "oldAiFunctionOidBodyAclOwnerPreserved": True,
    "oldWriterStageAndAttestorFunctionsUnchanged": True,
    "newVerifierRolesNoLoginAndNoMembers": True,
    "newProtectedKeyTableEmpty": True,
    "privateMacNotGrantedToPublisherAttestorWriterReader": True,
    "readerAndWriterTablePrivilegesUnchanged": True,
    "v11ReadyDeniedByRunAndCompleteGuards": True,
    "v11PositiveMacSeparatedToTargetPgTest":
        "ai_assistant.test_business_promotion_budget_v11_verifier_receipt_role",
    "beforeBackupRestored": True, "afterBackupRestored": True,
    "emptyReverseAndReapply": True, "productionWrites": False}
(folder / "business-promotion-budget-v11-verifier-upgrade-evidence.json").write_text(
    json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(result, ensure_ascii=False))
